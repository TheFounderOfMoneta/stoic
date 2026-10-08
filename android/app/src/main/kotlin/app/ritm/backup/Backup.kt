package app.ritm.backup

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import app.ritm.app
import app.ritm.data.RitmDb
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.time.LocalDate
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

/**
 * Резервная копия на телефоне: база, состояние геопозиции и голосовые заметки — один zip
 * в «Загрузки/Ритм». Восстановление — из такого же файла.
 */
object Backup {
    /** @return имя сохранённого файла. */
    suspend fun export(context: Context): String = withContext(Dispatchers.IO) {
        val db = context.app.repo.db
        // Сбросить журнал SQLite в основной файл, чтобы копия была целой.
        db.openHelper.writableDatabase.query("PRAGMA wal_checkpoint(FULL)").use { it.moveToFirst() }
        val name = "ritm-backup-${LocalDate.now()}.zip"
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, "application/zip")
            put(MediaStore.Downloads.RELATIVE_PATH, "Download/Ритм")
        }
        val uri = context.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: error("Не удалось создать файл в Загрузках")
        context.contentResolver.openOutputStream(uri)!!.use { out ->
            ZipOutputStream(out).use { zip ->
                val dbFile = context.getDatabasePath(RitmDb.NAME)
                zip.add("db/${RitmDb.NAME}", dbFile)
                context.filesDir.walkTopDown().filter { it.isFile }.forEach { f ->
                    zip.add("files/" + f.relativeTo(context.filesDir).path, f)
                }
            }
        }
        name
    }

    /** Восстановить из zip. После этого приложение нужно перезапустить. */
    suspend fun restore(context: Context, uri: Uri) = withContext(Dispatchers.IO) {
        val tmp = File(context.cacheDir, "restore").apply { deleteRecursively(); mkdirs() }
        context.contentResolver.openInputStream(uri)!!.use { input ->
            ZipInputStream(input).use { zip ->
                var e = zip.nextEntry
                while (e != null) {
                    val target = File(tmp, e.name).canonicalFile
                    require(target.path.startsWith(tmp.canonicalPath)) { "Плохой файл копии" }
                    if (!e.isDirectory) { target.parentFile?.mkdirs(); target.outputStream().use { zip.copyTo(it) } }
                    e = zip.nextEntry
                }
            }
        }
        val newDb = File(tmp, "db/${RitmDb.NAME}")
        require(newDb.exists()) { "В файле нет базы Ритма" }
        context.app.repo.db.close()
        val dbFile = context.getDatabasePath(RitmDb.NAME)
        listOf("", "-wal", "-shm").forEach { File(dbFile.path + it).delete() }
        newDb.copyTo(dbFile, overwrite = true)
        File(tmp, "files").takeIf { it.exists() }?.copyRecursively(context.filesDir, overwrite = true)
        tmp.deleteRecursively()
    }

    private fun ZipOutputStream.add(name: String, f: File) {
        if (!f.exists()) return
        putNextEntry(ZipEntry(name))
        f.inputStream().use { it.copyTo(this) }
        closeEntry()
    }
}
