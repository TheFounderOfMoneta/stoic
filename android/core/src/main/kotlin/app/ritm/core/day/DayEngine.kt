package app.ritm.core.day

import app.ritm.core.time.HOUR
import app.ritm.core.time.Interval
import app.ritm.core.time.minuteOfDay
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/**
 * День = от пробуждения до основного сна. Цикл (для калорий) = от пробуждения до следующего пробуждения.
 * @param sleepAfter основной сон, которым закончился день (null — день закрыт принудительно или ещё идёт)
 */
data class Day(
    val date: LocalDate,
    val start: Long,
    val end: Long?,
    val sleepAfter: Interval?,
    /** Основного сна не было — день закрыт в обычное время пробуждения или по пределу 30 ч. */
    val noSleep: Boolean,
) {
    val ongoing: Boolean get() = end == null
}

data class DayConfig(
    val forceCloseAfterMs: Long = 20 * HOUR,
    val hardCapMs: Long = 30 * HOUR,
    val defaultWakeMinute: Int = 7 * 60,
    /** Ночные сны с бодрствованием между ними меньше этого — одна ночь. */
    val sameNightGapMs: Long = 3 * HOUR,
    val medianWindow: Int = 14,
)

data class DayTimeline(
    val days: List<Day>,
    /** Сейчас идёт основной сон (день уже закрыт, новый не начался). */
    val sleepingSince: Long?,
) {
    val current: Day? get() = days.lastOrNull()?.takeIf { it.ongoing }
    /** Цикл дня: от его начала до начала следующего. */
    fun cycleOf(i: Int, now: Long): Interval = Interval(days[i].start, days.getOrNull(i + 1)?.start ?: now)
}

class DayEngine(private val cfg: DayConfig = DayConfig()) {

    /**
     * @param mainSleeps основные (ночные или подтверждённые) сны, любые по порядку
     * @param origin момент, с которого есть данные (установка приложения)
     * @param zoneAt часовой пояс в момент времени (для поездок)
     */
    fun build(mainSleeps: List<Interval>, origin: Long, now: Long, zoneAt: (Long) -> ZoneId): DayTimeline {
        val nights = groupNights(mainSleeps.filter { it.end > origin }.sortedBy { it.start })
        val days = mutableListOf<Day>()
        val wakeMinutes = mutableListOf<Int>()
        var cur = origin
        var sleepingSince: Long? = null
        var noSleepStart = false
        var idx = nights.indexOfFirst { it.start >= origin }.let { if (it < 0) nights.size else it }

        // Если данные начались посреди сна — день начинается с пробуждения.
        nights.firstOrNull { origin in it }?.let { n ->
            if (n.end < now) { cur = n.end; wakeMinutes += n.end.minuteOfDay(zoneAt(n.end)) } else sleepingSince = origin
            idx = nights.indexOf(n) + 1
        }

        while (sleepingSince == null) {
            val zone = zoneAt(cur)
            val median = median(wakeMinutes.takeLast(cfg.medianWindow)) ?: cfg.defaultWakeMinute
            val forced = minOf(nextAtMinute(median, cur + cfg.forceCloseAfterMs, zone), cur + cfg.hardCapMs)
            val next = nights.getOrNull(idx)
            // День, начатый не с пробуждения (первый после установки), датируем с поправкой на обычный подъём.
            val startedAtWake = days.isNotEmpty() || wakeMinutes.isNotEmpty()
            val dateBase = if (startedAtWake) cur else cur - median * 60_000L
            val date = Instant.ofEpochMilli(dateBase).atZone(zone).toLocalDate()

            if (next != null && next.start <= now && next.start <= forced) {
                days += Day(date, cur, next.start, next, noSleepStart)
                noSleepStart = false
                idx++
                if (next.end >= now) { sleepingSince = next.start; break }
                cur = next.end
                wakeMinutes += cur.minuteOfDay(zoneAt(cur))
            } else if (forced <= now) {
                days += Day(date, cur, forced, null, noSleepStart)
                noSleepStart = true
                cur = forced
            } else {
                days += Day(date, cur, null, null, noSleepStart)
                break
            }
        }
        return DayTimeline(days.map { d -> if (d.end != null && d.sleepAfter == null) d.copy(noSleep = true) else d }, sleepingSince)
    }

    /** Ночные сны с коротким бодрствованием между ними — одна ночь. */
    private fun groupNights(sleeps: List<Interval>): List<Interval> {
        val out = mutableListOf<Interval>()
        for (s in sleeps) {
            val last = out.lastOrNull()
            if (last != null && s.start - last.end <= cfg.sameNightGapMs) out[out.lastIndex] = Interval(last.start, maxOf(last.end, s.end))
            else out += s
        }
        return out
    }

    private fun median(xs: List<Int>): Int? {
        if (xs.isEmpty()) return null
        val s = xs.sorted()
        return s[s.size / 2]
    }

    /** Первый момент с местным временем minute, не раньше after. */
    private fun nextAtMinute(minute: Int, after: Long, zone: ZoneId): Long {
        val z = Instant.ofEpochMilli(after).atZone(zone)
        var candidate = z.toLocalDate().atTime(minute / 60, minute % 60).atZone(zone)
        if (candidate.toInstant().toEpochMilli() < after) candidate = candidate.plusDays(1)
        return candidate.toInstant().toEpochMilli()
    }
}
