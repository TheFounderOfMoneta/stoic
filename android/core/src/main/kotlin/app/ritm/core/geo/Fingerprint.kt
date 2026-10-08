package app.ritm.core.geo

/** Снимок радиоокружения: видимые Wi-Fi и сотовые вышки. */
data class RadioScan(
    val time: Long,
    val wifi: Set<String>,
    val cells: Set<String>,
    val connectedBssid: String? = null,
    /** Android пометил сеть как точку доступа телефона (metered hint). */
    val connectedIsHotspot: Boolean = false,
    /** Точка, где сделан снимок (если известна и надёжна). */
    val location: LatLon? = null,
)

data class PlaceFingerprint(
    val wifi: MutableMap<String, Int> = mutableMapOf(),
    val cells: MutableMap<String, Int> = mutableMapOf(),
    var scans: Int = 0,
)

/**
 * Отпечатки мест. Wi-Fi учитывается, только если сеть всегда видна в одном месте:
 * сеть, замеченная в точках дальше 1 км друг от друга, считается мобильной и исключается.
 */
class FingerprintBook(
    val places: MutableMap<Long, PlaceFingerprint> = mutableMapOf(),
    /** Где видели каждую сеть (несколько опорных точек). */
    val wifiSightings: MutableMap<String, MutableList<LatLon>> = mutableMapOf(),
    val mobileWifi: MutableSet<String> = mutableSetOf(),
    private val mobileSpreadM: Double = 1_000.0,
) {
    fun observe(scan: RadioScan) {
        val at = scan.location ?: return
        for (b in scan.wifi + listOfNotNull(scan.connectedBssid)) {
            if (b in mobileWifi) continue
            val seen = wifiSightings.getOrPut(b) { mutableListOf() }
            if (seen.any { distanceMeters(it, at) > mobileSpreadM }) {
                mobileWifi.add(b)
                wifiSightings.remove(b)
                places.values.forEach { it.wifi.remove(b) }
            } else if (seen.size < 5 && seen.none { distanceMeters(it, at) < 50 }) {
                seen.add(at)
            }
        }
        if (scan.connectedIsHotspot && scan.connectedBssid != null) {
            mobileWifi.add(scan.connectedBssid)
            places.values.forEach { it.wifi.remove(scan.connectedBssid) }
        }
    }

    /** Запомнить, что этот снимок сделан внутри места. */
    fun learn(placeId: Long, scan: RadioScan) {
        observe(scan)
        val fp = places.getOrPut(placeId) { PlaceFingerprint() }
        fp.scans++
        scan.wifi.filterNot { it in mobileWifi }.forEach { fp.wifi.merge(it, 1, Int::plus) }
        if (!scan.connectedIsHotspot) scan.connectedBssid?.takeIf { it !in mobileWifi }?.let { fp.wifi.merge(it, 1, Int::plus) }
        scan.cells.forEach { fp.cells.merge(it, 1, Int::plus) }
    }

    /** Узнать место без GPS. Возвращает id места или null. */
    fun match(scan: RadioScan): Long? {
        val wifi = scan.wifi.filterNot { it in mobileWifi }.toSet()
        val connected = scan.connectedBssid?.takeIf { it !in mobileWifi && !scan.connectedIsHotspot }
        var best: Long? = null
        var bestScore = 0.0
        for ((id, fp) in places) {
            if (fp.scans == 0) continue
            val stableWifi = fp.wifi.filterValues { it * 2 >= fp.scans || it >= 3 }.keys
            val stableCells = fp.cells.filterValues { it * 2 >= fp.scans || it >= 3 }.keys
            var score = 0.0
            if (connected != null && connected in stableWifi) score += 1.0
            if (stableWifi.isNotEmpty() && wifi.isNotEmpty()) {
                score += (wifi intersect stableWifi).size.toDouble() / minOf(wifi.size, stableWifi.size)
            }
            if (stableCells.isNotEmpty() && scan.cells.isNotEmpty()) {
                score += 0.5 * (scan.cells intersect stableCells).size.toDouble() / minOf(scan.cells.size, stableCells.size)
            }
            if (score > bestScore) { bestScore = score; best = id }
        }
        return if (bestScore >= 0.5) best else null
    }
}
