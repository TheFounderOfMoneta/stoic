package app.ritm.core.input

import kotlin.math.abs

/**
 * Кнопка Bixby на Samsung (S10+, One UI 4.1) через системный журнал.
 *
 * На каждое нажатие и каждое отпускание система пишет строку
 * `PhoneWindowManagerExt: getIntentBixbyService, ... interactive=<true|false> ...`.
 * Явного DOWN/UP в строке нет, поэтому строки чередуются: нажал, отпустил, нажал, отпустил…
 * Кроме того, ровно через 400 мс после отпускания иногда приходит служебная строка таймера Samsung:
 * при включённом экране у неё interactive=false (у нажатия/отпускания — true), при выключенном —
 * узнаём только по 400 мс. Служебную строку отбрасываем; после неё кнопка точно отпущена.
 */
class BixbyLogDecoder(
    private val timeoutMs: Long = 400,
    private val timeoutToleranceMs: Long = 20,
    /** Если «нажата» дольше этого — потеряли строку, считаем, что отпущена. */
    private val stuckMs: Long = 120_000,
) {
    enum class Edge { DOWN, UP }

    private var pressed = false
    private var lastAt = Long.MIN_VALUE
    private var lastWasUp = false

    val isPressed: Boolean get() = pressed

    /** @return нажатие/отпускание или null для служебной строки. */
    fun onLine(epochMs: Long, interactive: Boolean?): Edge? {
        val dt = if (lastAt == Long.MIN_VALUE) Long.MAX_VALUE else epochMs - lastAt
        if (!pressed && lastWasUp && interactive != true && abs(dt - timeoutMs) <= timeoutToleranceMs) {
            lastAt = epochMs
            lastWasUp = false
            return null
        }
        if (pressed && dt > stuckMs) pressed = false
        lastAt = epochMs
        return if (!pressed) {
            pressed = true; lastWasUp = false; Edge.DOWN
        } else {
            pressed = false; lastWasUp = true; Edge.UP
        }
    }
}

private val BIXBY_LINE = Regex("""getIntentBixbyService\b.*?interactive=(true|false)""")

/** Строка журнала про кнопку Bixby? Возвращает interactive или null, если строка не про кнопку. */
fun parseBixbyLine(message: String): Boolean? = BIXBY_LINE.find(message)?.groupValues?.get(1)?.toBooleanStrict()
