package app.ritm.core.steps

import app.ritm.core.time.Interval

/** Шаги за интервал. Внутри интервала шаги считаются распределёнными равномерно. */
data class StepInterval(val interval: Interval, val count: Int)

/** Шаги в интервале с пропорциональным делением пересекающихся записей. */
fun stepsIn(window: Interval, steps: List<StepInterval>): Int {
    var total = 0.0
    for (s in steps) {
        val ov = s.interval.overlap(window)
        if (ov <= 0) continue
        total += if (s.interval.duration == 0L) s.count.toDouble() else s.count * ov.toDouble() / s.interval.duration
    }
    return Math.round(total).toInt()
}

/** Шаги вне указанных интервалов (например, без шагов во время тренировок). */
fun stepsOutside(window: Interval, steps: List<StepInterval>, exclude: List<Interval>): Int {
    val inside = exclude.mapNotNull { e ->
        val s = maxOf(e.start, window.start); val en = minOf(e.end, window.end)
        if (en > s) Interval(s, en) else null
    }.sumOf { stepsIn(it, steps) }
    return (stepsIn(window, steps) - inside).coerceAtLeast(0)
}

/**
 * Аппаратный счётчик шагов: накопительное значение с момента загрузки.
 * Обнуляется только при перезагрузке — тогда новое значение меньше прошлого.
 */
class StepCounterTracker(
    var lastValue: Long? = null,
    var lastTime: Long? = null,
) {
    /**
     * @param value показание счётчика
     * @param time время последнего шага (из события датчика), мс UTC
     * @param bootTime время загрузки системы, мс UTC
     */
    fun onReading(value: Long, time: Long, bootTime: Long): StepInterval? {
        val prevValue = lastValue
        val prevTime = lastTime
        lastValue = value
        lastTime = time
        if (prevValue == null || prevTime == null) return null
        return if (value >= prevValue) {
            val delta = (value - prevValue).toInt()
            if (delta == 0 || time <= prevTime) null else StepInterval(Interval(prevTime, time), delta)
        } else {
            // Перезагрузка: всё, что насчитано с загрузки.
            val from = maxOf(bootTime, prevTime)
            if (value == 0L || time <= from) null else StepInterval(Interval(from, time), value.toInt())
        }
    }
}
