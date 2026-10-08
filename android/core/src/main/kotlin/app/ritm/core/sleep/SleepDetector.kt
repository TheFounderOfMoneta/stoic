package app.ritm.core.sleep

import app.ritm.core.time.HOUR
import app.ritm.core.time.Interval
import app.ritm.core.time.MINUTE
import app.ritm.core.time.coveredFraction
import app.ritm.core.time.minuteOfDay
import app.ritm.core.steps.StepInterval
import app.ritm.core.steps.stepsIn
import java.time.ZoneId

enum class Confidence { HIGH, MEDIUM, LOW }

data class SleepEstimate(
    val interval: Interval,
    val confidence: Confidence,
    val night: Boolean,
    /** Дневной сон засчитывается только после подтверждения. */
    val needsConfirmation: Boolean,
    val interruptions: Int,
    /** Сон ещё идёт (нет активности до сих пор). */
    val ongoing: Boolean,
)

data class SleepSignals(
    /** Моменты активности: касания, разблокировки, ввод на ПК. Отсортированы. */
    val activity: List<Long>,
    val charging: List<Interval> = emptyList(),
    val dark: List<Interval> = emptyList(),
    val atHome: List<Interval> = emptyList(),
    val still: List<Interval> = emptyList(),
    val steps: List<StepInterval> = emptyList(),
    /** Когда агент ПК был на связи (знаем, был ли ввод на ПК). */
    val pcCovered: List<Interval> = emptyList(),
)

data class SleepConfig(
    val minSleepMs: Long = 2 * HOUR,
    val maxTouchMs: Long = 5 * MINUTE,
    val quietAroundTouchMs: Long = 30 * MINUTE,
    val burstGapMs: Long = 2 * MINUTE,
    val nightWakeMergeMs: Long = 45 * MINUTE,
    val mergePieceMinMs: Long = HOUR,
    val maxStepsInSleep: Int = 300,
    /** Ночь: середина сна между 22:00 и 10:00. */
    val nightStartMinute: Int = 22 * 60,
    val nightEndMinute: Int = 10 * 60,
)

/**
 * Сон = нет касаний/ввода на телефоне и ПК ≥ 2 ч. Касания до 5 минут сон не прерывают.
 * Ночные пробуждения до 45 минут склеиваются. Это оценка — с уровнем уверенности.
 */
class SleepDetector(private val cfg: SleepConfig = SleepConfig()) {

    private data class Burst(val start: Long, val end: Long)
    private data class Rest(val start: Long, val end: Long, val interruptions: Int)

    fun detect(signals: SleepSignals, now: Long, zone: ZoneId): List<SleepEstimate> {
        val pings = signals.activity.filter { it <= now }.sorted()
        if (pings.isEmpty()) return emptyList()

        val bursts = mutableListOf<Burst>()
        for (t in pings) {
            val b = bursts.lastOrNull()
            if (b != null && t - b.end <= cfg.burstGapMs) bursts[bursts.lastIndex] = b.copy(end = t)
            else bursts.add(Burst(t, t))
        }

        // Промежутки без активности. Короткое касание (≤ 5 мин) внутри не считается пробуждением,
        // если до и после него было ≥ 30 мин покоя (глянул время ночью — да, переписка раз в 10 мин — нет).
        val gaps = bursts.indices.map { i -> Interval(bursts[i].end, if (i + 1 < bursts.size) bursts[i + 1].start else now) }
        val rests = mutableListOf<Rest>()
        var cur: Rest? = null
        for (i in gaps.indices) {
            val g = gaps[i]
            if (g.duration <= 0) { cur?.let(rests::add); cur = null; continue }
            val c = cur
            if (c == null) { cur = Rest(g.start, g.end, 0); continue }
            val between = bursts[i]
            val absorb = between.end - between.start <= cfg.maxTouchMs &&
                gaps[i - 1].duration >= cfg.quietAroundTouchMs && g.duration >= cfg.quietAroundTouchMs
            if (absorb) cur = c.copy(end = g.end, interruptions = c.interruptions + 1)
            else { rests.add(c); cur = Rest(g.start, g.end, 0) }
        }
        cur?.let(rests::add)

        // Склеиваем ночные куски (каждый ≥ 1 ч), если между ними ≤ 45 мин бодрствования.
        val pieces = rests.filter { it.end - it.start >= cfg.mergePieceMinMs }
        val merged = mutableListOf<Rest>()
        for (p in pieces) {
            val prev = merged.lastOrNull()
            if (prev != null && p.start - prev.end <= cfg.nightWakeMergeMs &&
                isNight(Interval(prev.start, p.end).midpoint(), zone)
            ) {
                merged[merged.lastIndex] = Rest(prev.start, p.end, prev.interruptions + p.interruptions + 1)
            } else {
                merged.add(p)
            }
        }

        return merged.filter { it.end - it.start >= cfg.minSleepMs }.mapNotNull { r ->
            val iv = Interval(r.start, r.end)
            val steps = stepsIn(iv, signals.steps)
            if (steps > cfg.maxStepsInSleep) return@mapNotNull null
            val night = isNight(iv.midpoint(), zone)
            val home = iv.coveredFraction(signals.atHome) >= 0.7
            val chargingOrDark = maxOf(iv.coveredFraction(signals.charging), iv.coveredFraction(signals.dark)) >= 0.5
            val still = iv.coveredFraction(signals.still) >= 0.7 || steps < 50
            val pcKnown = iv.coveredFraction(signals.pcCovered) >= 0.8
            val confidence = when {
                night && home && chargingOrDark && still && pcKnown -> Confidence.HIGH
                night && home -> Confidence.MEDIUM
                else -> Confidence.LOW
            }
            SleepEstimate(iv, confidence, night, needsConfirmation = !night, interruptions = r.interruptions, ongoing = r.end >= now)
        }
    }

    fun isNight(t: Long, zone: ZoneId): Boolean {
        val m = t.minuteOfDay(zone)
        return m >= cfg.nightStartMinute || m < cfg.nightEndMinute
    }
}
