package app.ritm.voice

import android.content.Context
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import app.ritm.app
import app.ritm.data.json
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.vosk.Model
import org.vosk.Recognizer
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.zip.ZipInputStream

/**
 * Расшифровка голосовых заметок прямо на телефоне, без интернета (Vosk, русская модель ~45 МБ).
 * Модель скачивается один раз по кнопке в настройках.
 */
object Transcriber {
    private const val MODEL = "vosk-model-small-ru-0.22"
    private const val URL_ZIP = "https://alphacephei.com/vosk/models/$MODEL.zip"

    /** null — не качается; 0..1 — прогресс; -1 — ошибка. */
    val download = MutableStateFlow<Float?>(null)

    private fun root(ctx: Context) = File(ctx.filesDir, "vosk")
    fun modelDir(ctx: Context) = File(root(ctx), MODEL)
    fun ready(ctx: Context) = File(modelDir(ctx), "am").isDirectory

    suspend fun downloadModel(ctx: Context): Boolean = withContext(Dispatchers.IO) {
        download.value = 0f
        val tmp = File(ctx.cacheDir, "$MODEL.zip")
        try {
            val c = URL(URL_ZIP).openConnection() as HttpURLConnection
            c.connectTimeout = 15_000; c.readTimeout = 60_000
            val total = c.contentLengthLong.takeIf { it > 0 } ?: 45_000_000L
            c.inputStream.use { input ->
                tmp.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    var done = 0L
                    while (true) {
                        val n = input.read(buf); if (n < 0) break
                        out.write(buf, 0, n); done += n
                        download.value = (done.toFloat() / total).coerceIn(0f, 0.99f)
                    }
                }
            }
            c.disconnect()
            val dest = root(ctx).apply { deleteRecursively(); mkdirs() }
            ZipInputStream(tmp.inputStream()).use { zip ->
                var e = zip.nextEntry
                while (e != null) {
                    val f = File(dest, e.name).canonicalFile
                    require(f.path.startsWith(dest.canonicalPath))
                    if (e.isDirectory) f.mkdirs() else { f.parentFile?.mkdirs(); f.outputStream().use { zip.copyTo(it) } }
                    e = zip.nextEntry
                }
            }
            tmp.delete()
            download.value = null
            ready(ctx)
        } catch (e: Exception) {
            tmp.delete()
            download.value = -1f
            false
        }
    }

    /** Аудио m4a → текст. Пустая строка — ничего не разобрано. */
    suspend fun transcribe(ctx: Context, file: File): String = withContext(Dispatchers.Default) {
        val model = Model(modelDir(ctx).absolutePath)
        var rec: Recognizer? = null
        val ex = MediaExtractor()
        try {
            ex.setDataSource(file.absolutePath)
            val track = (0 until ex.trackCount).first { ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true }
            ex.selectTrack(track)
            val fmt = ex.getTrackFormat(track)
            var rate = fmt.getInteger(MediaFormat.KEY_SAMPLE_RATE)
            var channels = fmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
            val codec = MediaCodec.createDecoderByType(fmt.getString(MediaFormat.KEY_MIME)!!)
            codec.configure(fmt, null, null, 0)
            codec.start()
            val info = MediaCodec.BufferInfo()
            var inDone = false
            var outDone = false
            while (!outDone) {
                if (!inDone) {
                    val i = codec.dequeueInputBuffer(10_000)
                    if (i >= 0) {
                        val n = ex.readSampleData(codec.getInputBuffer(i)!!, 0)
                        if (n < 0) { codec.queueInputBuffer(i, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM); inDone = true }
                        else { codec.queueInputBuffer(i, 0, n, ex.sampleTime, 0); ex.advance() }
                    }
                }
                val o = codec.dequeueOutputBuffer(info, 10_000)
                if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    rate = codec.outputFormat.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                    channels = codec.outputFormat.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                } else if (o >= 0) {
                    val out = codec.getOutputBuffer(o)!!
                    val bytes = ByteArray(info.size)
                    out.position(info.offset); out.get(bytes)
                    codec.releaseOutputBuffer(o, false)
                    val r = rec ?: Recognizer(model, rate.toFloat()).also { rec = it }
                    val mono = if (channels > 1) downmix(bytes, channels) else bytes
                    if (mono.isNotEmpty()) r.acceptWaveForm(mono, mono.size)
                    if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) outDone = true
                }
            }
            codec.stop(); codec.release()
            val result = rec?.finalResult ?: return@withContext ""
            json.parseToJsonElement(result).jsonObject["text"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
                .replaceFirstChar { it.uppercase() }
        } finally {
            ex.release(); rec?.close(); model.close()
        }
    }

    /** 16-бит PCM, несколько каналов → один. */
    private fun downmix(pcm: ByteArray, channels: Int): ByteArray {
        val frames = pcm.size / (2 * channels)
        val out = ByteArray(frames * 2)
        for (f in 0 until frames) {
            var sum = 0
            for (c in 0 until channels) {
                val i = (f * channels + c) * 2
                sum += (pcm[i].toInt() and 0xFF) or (pcm[i + 1].toInt() shl 8)
            }
            val v = (sum / channels).coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
            out[f * 2] = (v and 0xFF).toByte(); out[f * 2 + 1] = ((v shr 8) and 0xFF).toByte()
        }
        return out
    }

    /** Поставить расшифровку в очередь (если модель скачана). */
    fun enqueue(ctx: Context, noteId: Long) {
        if (!ready(ctx)) return
        WorkManager.getInstance(ctx).enqueueUniqueWork("transcribe-$noteId", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<TranscribeWorker>().setInputData(workDataOf("id" to noteId)).build())
    }

    /** Расшифровать все старые голосовые без текста. */
    suspend fun enqueueAll(ctx: Context) {
        ctx.app.repo.db.notes().untranscribed().forEach { enqueue(ctx, it.id) }
    }
}

class TranscribeWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val id = inputData.getLong("id", -1)
        val repo = applicationContext.app.repo
        val note = repo.db.notes().byId(id) ?: return Result.success()
        val path = note.audioPath ?: return Result.success()
        if (note.text.isNotBlank() || !Transcriber.ready(applicationContext)) return Result.success()
        val text = runCatching { Transcriber.transcribe(applicationContext, File(path)) }.getOrElse { return Result.retry() }
        if (text.isBlank()) return Result.success()
        repo.db.notes().update(note.copy(text = text))
        repo.events.emit("note.transcript") { put("uid", note.uid); put("text", text) }
        return Result.success()
    }
}
