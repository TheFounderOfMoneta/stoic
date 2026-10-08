package app.ritm.button

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.SystemClock
import app.ritm.core.input.BixbyLogDecoder
import app.ritm.core.input.parseBixbyLine
import kotlinx.coroutines.flow.MutableStateFlow
import java.io.BufferedReader
import java.io.InputStreamReader
import kotlin.concurrent.thread

/** Команда, которую нужно один раз выполнить с компьютера. На Android 12 доступ переживает перезагрузку. */
const val READ_LOGS_COMMAND = "adb shell pm grant app.ritm android.permission.READ_LOGS"

fun canReadLogs(context: Context): Boolean =
    context.checkSelfPermission(Manifest.permission.READ_LOGS) == PackageManager.PERMISSION_GRANTED

/**
 * Кнопка Bixby через системный журнал (S10+, One UI 4.1): спецвозможностям система её не отдаёт,
 * а в журнал пишет каждое нажатие и отпускание. Читаем только один тег — фильтрует сама система,
 * поэтому почти без расхода батареи. Из журнала берутся только строки про кнопку; ничего не сохраняется
 * и никуда не отправляется.
 */
object LogcatKeySource {
    private const val TAG = "PhoneWindowManagerExt"
    private val LINE = Regex("""^\s*(\d+)\.(\d{3})\d*\s+\d+\s+\d+\s+[VDIWEFA]\s+(.+?)\s*:\s?(.*)$""")

    val active = MutableStateFlow(false)
    @Volatile private var running = false
    @Volatile private var proc: Process? = null

    fun start(context: Context) {
        if (running || !canReadLogs(context)) return
        running = true
        val app = context.applicationContext
        thread(name = "ritm-bixby-log", isDaemon = true) {
            val decoder = BixbyLogDecoder()
            while (running) {
                try {
                    val p = ProcessBuilder("logcat", "-v", "epoch", "-T", "1", "-s", "$TAG:D").redirectErrorStream(true).start()
                    proc = p
                    active.value = true
                    BufferedReader(InputStreamReader(p.inputStream)).useLines { lines ->
                        for (raw in lines) {
                            if (!running) break
                            val m = LINE.find(raw) ?: continue
                            val (sec, ms, _, msg) = m.destructured
                            val interactive = parseBixbyLine(msg) ?: continue
                            val epoch = sec.toLong() * 1000 + ms.toLong()
                            val edge = decoder.onLine(epoch, interactive) ?: continue
                            // Время события в шкале uptime: журнал пишет почти мгновенно, учитываем задержку чтения.
                            val uptime = SystemClock.uptimeMillis() - (System.currentTimeMillis() - epoch).coerceIn(0, 2_000)
                            when (edge) {
                                BixbyLogDecoder.Edge.DOWN -> ButtonBrain.down(app, "журнал", uptime)
                                BixbyLogDecoder.Edge.UP -> ButtonBrain.up(app, "журнал", uptime)
                            }
                        }
                    }
                } catch (_: Exception) {
                }
                active.value = false
                if (running) Thread.sleep(2_000)
            }
        }
    }

    fun stop() {
        running = false
        proc?.destroy()
        proc = null
        active.value = false
    }
}
