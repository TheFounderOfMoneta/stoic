package app.ritm.core.geo

import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

data class LatLon(val lat: Double, val lon: Double)

private const val EARTH_RADIUS_M = 6_371_008.8

/** Расстояние по большому кругу (гаверсинус), метры. */
fun distanceMeters(a: LatLon, b: LatLon): Double {
    val dLat = Math.toRadians(b.lat - a.lat)
    val dLon = Math.toRadians(b.lon - a.lon)
    val h = sin(dLat / 2).pow(2) +
        cos(Math.toRadians(a.lat)) * cos(Math.toRadians(b.lat)) * sin(dLon / 2).pow(2)
    return 2 * EARTH_RADIUS_M * asin(sqrt(h.coerceIn(0.0, 1.0)))
}

/**
 * Центр по набору точек: взвешенное среднее с весом 1/точность²,
 * затем повтор без выбросов дальше 2σ (или 2× медианной точности) от первого центра.
 */
fun weightedCenter(points: List<Pair<LatLon, Double>>): LatLon {
    require(points.isNotEmpty())
    fun mean(ps: List<Pair<LatLon, Double>>): LatLon {
        var w = 0.0; var lat = 0.0; var lon = 0.0
        for ((p, acc) in ps) {
            val wi = 1.0 / (acc.coerceAtLeast(3.0)).pow(2)
            w += wi; lat += p.lat * wi; lon += p.lon * wi
        }
        return LatLon(lat / w, lon / w)
    }
    val first = mean(points)
    if (points.size < 3) return first
    val dists = points.map { distanceMeters(first, it.first) }
    val sigma = sqrt(dists.sumOf { it * it } / dists.size)
    val medianAcc = points.map { it.second }.sorted()[points.size / 2]
    val limit = maxOf(2 * sigma, 2 * medianAcc, 15.0)
    val kept = points.filterIndexed { i, _ -> dists[i] <= limit }
    return if (kept.isEmpty()) first else mean(kept)
}
