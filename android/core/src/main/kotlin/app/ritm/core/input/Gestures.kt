package app.ritm.core.input

sealed interface Gesture {
    data class Taps(val count: Int) : Gesture
    data object HoldStart : Gesture
    data class HoldEnd(val durationMs: Long) : Gesture
}

/**
 * Жесты одной кнопки: 1/2/3 нажатия и удержание.
 * Чистая логика: платформа зовёт onDown/onUp и onTick в момент nextDeadline().
 */
class PressGestureDetector(
    private val tapWindowMs: Long = 350,
    private val holdMs: Long = 450,
    private val maxTaps: Int = 3,
) {
    private var downAt: Long? = null
    private var taps = 0
    private var lastUpAt = 0L
    private var holding = false

    fun onDown(t: Long): List<Gesture> {
        if (downAt != null) return emptyList()
        downAt = t
        return emptyList()
    }

    fun onUp(t: Long): List<Gesture> {
        val d = downAt ?: return emptyList()
        downAt = null
        if (holding) {
            holding = false
            taps = 0
            return listOf(Gesture.HoldEnd(t - d))
        }
        taps++
        lastUpAt = t
        if (taps >= maxTaps) {
            taps = 0
            return listOf(Gesture.Taps(maxTaps))
        }
        return emptyList()
    }

    fun onTick(t: Long): List<Gesture> {
        val d = downAt
        if (d != null && !holding && t - d >= holdMs) {
            holding = true
            taps = 0
            return listOf(Gesture.HoldStart)
        }
        if (d == null && taps > 0 && t - lastUpAt >= tapWindowMs) {
            val c = taps
            taps = 0
            return listOf(Gesture.Taps(c))
        }
        return emptyList()
    }

    fun nextDeadline(): Long? {
        val d = downAt
        return when {
            d != null && !holding -> d + holdMs
            d == null && taps > 0 -> lastUpAt + tapWindowMs
            else -> null
        }
    }
}

/** Защита от кармана: при выключенном экране и закрытом датчике приближения — игнор, кроме тренировки. */
fun shouldIgnorePress(screenOn: Boolean, proximityNear: Boolean, workoutActive: Boolean): Boolean =
    !workoutActive && !screenOn && proximityNear

/** Голосовая заметка: удержание короче полсекунды — случайное. */
fun isVoiceNoteValid(holdDurationMs: Long): Boolean = holdDurationMs >= 500

enum class SingleAction { WORKOUT_SET, WEIGHT, PLUS }

/** Одно нажатие = верхнее действие: тренировка → вес → «+». */
fun singlePressAction(workoutActive: Boolean, weightCardVisible: Boolean): SingleAction = when {
    workoutActive -> SingleAction.WORKOUT_SET
    weightCardVisible -> SingleAction.WEIGHT
    else -> SingleAction.PLUS
}
