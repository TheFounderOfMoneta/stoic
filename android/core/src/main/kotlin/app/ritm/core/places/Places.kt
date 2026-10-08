package app.ritm.core.places

import app.ritm.core.geo.LatLon
import app.ritm.core.geo.distanceMeters
import app.ritm.core.geo.weightedCenter
import app.ritm.core.time.HOUR
import app.ritm.core.time.Interval
import app.ritm.core.time.MINUTE
import java.time.DayOfWeek
import java.time.Instant
import java.time.ZoneId

data class Place(val id: Long, val name: String, val center: LatLon, val radiusM: Double = 200.0)

/** Место, в котором находится точка: из содержащих — ближайшее по центру. */
fun placeAt(point: LatLon, places: List<Place>): Place? =
    places.map { it to distanceMeters(it.center, point) }
        .filter { (p, d) -> d <= p.radiusM }
        .minByOrNull { it.second }?.first

data class Stay(val interval: Interval, val center: LatLon)

sealed interface StayEvent {
    /** Стоянка длится дольше порога — можно предлагать сохранить место. */
    data class LongStay(val start: Long, val center: LatLon) : StayEvent
    data class Ended(val stay: Stay) : StayEvent
}

data class StayConfig(
    val radiusM: Double = 200.0,
    val suggestAfterMs: Long = HOUR,
    val maxExitMs: Long = 10 * MINUTE,
    val minStayMs: Long = 10 * MINUTE,
    val stillStepsPerTick: Int = 30,
)

/**
 * Детектор стоянок: центр — первая точка остановки, внутри — всё в радиусе 200 м от неё.
 * Выходы до 10 минут не сбрасывают стоянку.
 */
class StayDetector(private val cfg: StayConfig = StayConfig()) {
    private var anchor: LatLon? = null
    private var start = 0L
    private var lastInside = 0L
    private var outsideSince: Long? = null
    private var firstOutside: Pair<LatLon, Double>? = null
    private val points = mutableListOf<Pair<LatLon, Double>>()
    private var announced = false

    /** Принятая фильтром точка, пригодная для мест. */
    fun onFix(time: Long, point: LatLon, accuracyM: Double): List<StayEvent> {
        val a = anchor
        if (a == null) {
            begin(time, point, accuracyM)
            return emptyList()
        }
        if (distanceMeters(a, point) <= cfg.radiusM) {
            outsideSince = null
            firstOutside = null
            lastInside = time
            points.add(point to accuracyM)
            return check(time)
        }
        val out = outsideSince
        if (out == null) {
            outsideSince = time
            firstOutside = point to accuracyM
            return emptyList()
        }
        if (time - out > cfg.maxExitMs) {
            val ended = end(out)
            val (p, acc) = firstOutside ?: (point to accuracyM)
            begin(out, p, acc)
            if (distanceMeters(p, point) <= cfg.radiusM) {
                lastInside = time; points.add(point to accuracyM)
            } else {
                begin(time, point, accuracyM)
            }
            return ended
        }
        return emptyList()
    }

    /** Периодическая отметка без точки: если стоим и почти нет шагов — мы всё ещё на месте. */
    fun onTick(time: Long, stepsSinceLastTick: Int, still: Boolean): List<StayEvent> {
        if (anchor == null) return emptyList()
        val out = outsideSince
        if (out != null) {
            if (time - out > cfg.maxExitMs) {
                val ended = end(out)
                reset()
                return ended
            }
            return emptyList()
        }
        if (still && stepsSinceLastTick <= cfg.stillStepsPerTick) {
            lastInside = time
            return check(time)
        }
        return emptyList()
    }

    fun currentCenter(): LatLon? = if (points.isEmpty()) anchor else weightedCenter(points)

    private fun check(time: Long): List<StayEvent> {
        if (!announced && lastInside - start >= cfg.suggestAfterMs) {
            announced = true
            return listOf(StayEvent.LongStay(start, currentCenter()!!))
        }
        return emptyList()
    }

    private fun begin(time: Long, point: LatLon, accuracyM: Double) {
        anchor = point; start = time; lastInside = time
        outsideSince = null; firstOutside = null
        points.clear(); points.add(point to accuracyM)
        announced = false
    }

    private fun end(at: Long): List<StayEvent> {
        val endTime = minOf(at, maxOf(lastInside, start))
        val c = currentCenter() ?: return emptyList()
        return if (endTime - start >= cfg.minStayMs) listOf(StayEvent.Ended(Stay(Interval(start, endTime), c))) else emptyList()
    }

    private fun reset() {
        anchor = null; outsideSince = null; firstOutside = null; points.clear(); announced = false
    }
}

/** Решение: показывать ли карточку «Сохранить место?». */
fun shouldSuggestPlace(center: LatLon, places: List<Place>, dismissed: List<LatLon>, radiusM: Double = 200.0): Boolean =
    placeAt(center, places) == null && dismissed.none { distanceMeters(it, center) <= radiusM }

enum class PlaceKind(val title: String) { HOME("Дом"), WORK("Работа"), GYM("Зал") }

/**
 * Угадать название по истории стоянок в этой точке:
 * ночевал → Дом; будни днём ≥ 4 ч (2+ раза) → Работа; 45–150 мин регулярно (2+ раза) → Зал.
 */
fun guessPlaceKind(center: LatLon, stays: List<Stay>, zone: ZoneId, radiusM: Double = 200.0): PlaceKind? {
    val here = stays.filter { distanceMeters(it.center, center) <= radiusM }
    if (here.isEmpty()) return null
    fun coversNight(s: Stay): Boolean {
        val startDate = Instant.ofEpochMilli(s.interval.start).atZone(zone).toLocalDate()
        return (0..1).any { off ->
            val t = startDate.plusDays(off.toLong()).atTime(3, 0).atZone(zone).toInstant().toEpochMilli()
            t in s.interval
        }
    }
    if (here.any(::coversNight)) return PlaceKind.HOME
    val work = here.count {
        val z = Instant.ofEpochMilli(it.interval.start).atZone(zone)
        z.dayOfWeek !in setOf(DayOfWeek.SATURDAY, DayOfWeek.SUNDAY) && z.hour in 7..12 && it.interval.duration >= 4 * HOUR
    }
    if (work >= 2) return PlaceKind.WORK
    val gym = here.count { it.interval.duration in (45 * MINUTE)..(150 * MINUTE) }
    if (gym >= 2) return PlaceKind.GYM
    return null
}
