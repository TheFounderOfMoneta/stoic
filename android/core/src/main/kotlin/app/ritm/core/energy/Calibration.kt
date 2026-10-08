package app.ritm.core.energy

import java.time.LocalDate
import java.time.temporal.ChronoUnit

/** Вес трендом: экспоненциальное сглаживание по дням (α = 0.1), дни без взвешивания держат тренд. */
fun weightTrend(weights: List<Pair<LocalDate, Double>>, alpha: Double = 0.1): Map<LocalDate, Double> {
    if (weights.isEmpty()) return emptyMap()
    val byDay = weights.groupBy({ it.first }, { it.second }).mapValues { (_, v) -> v.average() }.toSortedMap()
    val out = LinkedHashMap<LocalDate, Double>()
    var trend = byDay.values.first()
    var d = byDay.firstKey()
    val last = byDay.lastKey()
    while (!d.isAfter(last)) {
        byDay[d]?.let { trend += alpha * (it - trend) }
        out[d] = trend
        d = d.plusDays(1)
    }
    return out
}

fun trendAt(trend: Map<LocalDate, Double>, date: LocalDate): Double? =
    trend.entries.lastOrNull { !it.key.isAfter(date) }?.value

data class EnergyDay(val date: LocalDate, val intake: Double, val estimated: Double, val complete: Boolean)

data class Calibration(val factor: Double, val realTdee: Double, val estimatedTdee: Double, val days: Int)

data class CalibrationConfig(
    val minHistoryDays: Long = 21,
    val windowDays: Long = 28,
    val minWeighIns: Int = 10,
    val minCompleteDays: Int = 14,
    val minFactor: Double = 0.75,
    val maxFactor: Double = 1.25,
)

/**
 * Самокалибровка: сравнить расчётную потерю веса с реальной (по тренду) и поправить расход.
 * Неполные дни (пропуски в еде) исключаются.
 */
fun calibrate(
    days: List<EnergyDay>,
    weights: List<Pair<LocalDate, Double>>,
    today: LocalDate,
    cfg: CalibrationConfig = CalibrationConfig(),
): Calibration? {
    val first = days.minOfOrNull { it.date } ?: return null
    if (ChronoUnit.DAYS.between(first, today) < cfg.minHistoryDays) return null
    val from = today.minusDays(cfg.windowDays)
    val window = days.filter { !it.date.isBefore(from) && it.date.isBefore(today) && it.complete }
    if (window.size < cfg.minCompleteDays) return null
    if (weights.count { !it.first.isBefore(from) && it.first.isBefore(today) } < cfg.minWeighIns) return null

    // Наклон прямой по взвешиваниям окна (кг/день): тренд без отставания, шум взвешиваний гасится.
    val inWindow = weights.filter { !it.first.isBefore(from) && it.first.isBefore(today) }
    val xs = inWindow.map { ChronoUnit.DAYS.between(from, it.first).toDouble() }
    val ys = inWindow.map { it.second }
    val mx = xs.average()
    val my = ys.average()
    val sxx = xs.sumOf { (it - mx) * (it - mx) }
    if (sxx <= 0) return null
    val slope = xs.indices.sumOf { (xs[it] - mx) * (ys[it] - my) } / sxx
    val real = window.map { it.intake }.average() - slope * Energy.KCAL_PER_KG
    val est = window.map { it.estimated }.average()
    if (est <= 0) return null
    return Calibration((real / est).coerceIn(cfg.minFactor, cfg.maxFactor), real, est, window.size)
}
