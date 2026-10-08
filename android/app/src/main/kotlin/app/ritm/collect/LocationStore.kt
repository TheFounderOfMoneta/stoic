package app.ritm.collect

import android.content.Context
import app.ritm.core.geo.FilterState
import app.ritm.core.geo.FingerprintBook
import app.ritm.core.geo.Fix
import app.ritm.core.geo.LatLon
import app.ritm.core.geo.PlaceFingerprint
import app.ritm.core.geo.Provider
import app.ritm.data.json
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import java.io.File

@Serializable private data class LL(val lat: Double, val lon: Double)
@Serializable private data class FixDto(val t: Long, val lat: Double, val lon: Double, val acc: Double, val p: String)
@Serializable private data class FilterDto(
    val last: FixDto? = null,
    val quarantine: List<FixDto> = emptyList(),
    val qSteps: Int = 0,
    val qVehicle: Boolean = false,
    val candidates: List<Pair<LL, Int>> = emptyList(),
    val zones: List<LL> = emptyList(),
)
@Serializable private data class FpDto(val wifi: Map<String, Int>, val cells: Map<String, Int>, val scans: Int)
@Serializable private data class BookDto(
    val places: Map<Long, FpDto> = emptyMap(),
    val sightings: Map<String, List<LL>> = emptyMap(),
    val mobile: Set<String> = emptySet(),
)

/** Сохранение состояния фильтра (включая зоны подмены) и отпечатков мест между перезапусками. */
class LocationStore(context: Context) {
    private val filterFile = File(context.filesDir, "geo-filter.json")
    private val bookFile = File(context.filesDir, "geo-fingerprints.json")

    private fun Fix.dto() = FixDto(time, point.lat, point.lon, accuracyM, provider.name)
    private fun FixDto.fix() = Fix(t, LatLon(lat, lon), acc, runCatching { Provider.valueOf(p) }.getOrDefault(Provider.FUSED))
    private fun LatLon.ll() = LL(lat, lon)
    private fun LL.latLon() = LatLon(lat, lon)

    fun loadFilter(): FilterState = runCatching {
        val d = json.decodeFromString<FilterDto>(filterFile.readText())
        FilterState(d.last?.fix(), d.quarantine.map { it.fix() }, d.qSteps, d.qVehicle,
            d.candidates.map { it.first.latLon() to it.second }, d.zones.map { it.latLon() })
    }.getOrDefault(FilterState())

    fun saveFilter(s: FilterState) {
        val d = FilterDto(s.last?.dto(), s.quarantine.map { it.dto() }, s.quarantineSteps, s.quarantineVehicle,
            s.spoofCandidates.map { it.first.ll() to it.second }, s.spoofZones.map { it.ll() })
        filterFile.writeText(json.encodeToString(FilterDto.serializer(), d))
    }

    fun loadBook(): FingerprintBook = runCatching {
        val d = json.decodeFromString<BookDto>(bookFile.readText())
        FingerprintBook(
            places = d.places.mapValues { (_, f) -> PlaceFingerprint(f.wifi.toMutableMap(), f.cells.toMutableMap(), f.scans) }.toMutableMap(),
            wifiSightings = d.sightings.mapValues { (_, v) -> v.map { it.latLon() }.toMutableList() }.toMutableMap(),
            mobileWifi = d.mobile.toMutableSet(),
        )
    }.getOrDefault(FingerprintBook())

    fun saveBook(b: FingerprintBook) {
        val d = BookDto(
            places = b.places.mapValues { (_, f) -> FpDto(f.wifi.toMap(), f.cells.toMap(), f.scans) },
            sightings = b.wifiSightings.mapValues { (_, v) -> v.map { it.ll() } },
            mobile = b.mobileWifi.toSet(),
        )
        bookFile.writeText(json.encodeToString(BookDto.serializer(), d))
    }
}
