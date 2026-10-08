package app.ritm.core.time

import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId

const val MINUTE = 60_000L
const val HOUR = 60 * MINUTE
const val DAY = 24 * HOUR

/** Полуоткрытый интервал [start, end) в миллисекундах UTC. */
data class Interval(val start: Long, val end: Long) {
    init { require(end >= start) { "end < start: $start..$end" } }

    val duration: Long get() = end - start

    fun overlap(other: Interval): Long =
        (minOf(end, other.end) - maxOf(start, other.start)).coerceAtLeast(0)

    operator fun contains(t: Long): Boolean = t in start until end

    fun midpoint(): Long = start + duration / 2
}

/** Доля интервала, покрытая набором интервалов (интервалы могут пересекаться). */
fun Interval.coveredFraction(cover: List<Interval>): Double {
    if (duration == 0L) return 0.0
    val merged = cover.filter { it.overlap(this) > 0 }
        .map { Interval(maxOf(it.start, start), minOf(it.end, end)) }
        .sortedBy { it.start }
        .fold(mutableListOf<Interval>()) { acc, iv ->
            val last = acc.lastOrNull()
            if (last != null && iv.start <= last.end) acc[acc.lastIndex] = Interval(last.start, maxOf(last.end, iv.end))
            else acc.add(iv)
            acc
        }
    return merged.sumOf { it.duration }.toDouble() / duration
}

fun Long.localTime(zone: ZoneId): LocalTime = Instant.ofEpochMilli(this).atZone(zone).toLocalTime()

/** Минуты от полуночи по местному времени. */
fun Long.minuteOfDay(zone: ZoneId): Int = localTime(zone).let { it.hour * 60 + it.minute }
