package app.ritm.core.geo

import app.ritm.core.time.HOUR
import app.ritm.core.time.MINUTE
import kotlin.math.abs

enum class Provider { GPS, NETWORK, FUSED, PASSIVE }

enum class Motion { STILL, WALKING, RUNNING, BICYCLE, VEHICLE, UNKNOWN }

/**
 * Точка местоположения. Поля gnss* заполняются, если точка получена со спутников
 * и в этот момент был доступен статус GNSS (для эвристик подмены).
 */
data class Fix(
    val time: Long,
    val point: LatLon,
    val accuracyM: Double,
    val provider: Provider,
    val isMock: Boolean = false,
    /** Время GNSS минус системное время, мс. */
    val gnssTimeSkewMs: Long? = null,
    val satellites: Int? = null,
    val cn0MeanDb: Double? = null,
    val cn0StdDevDb: Double? = null,
)

/** Что происходило между прошлой принятой точкой и этой. */
data class MotionContext(
    val stepsSinceLast: Int,
    val motion: Motion,
    val vehicleSinceLast: Boolean = motion == Motion.VEHICLE,
)

enum class RejectReason { MOCK, SPOOF_ZONE, GNSS_SIGNAL, NETWORK_DISAGREES, STILL_JUMP }

sealed interface Verdict {
    data class Accepted(val fix: Fix, val usableForPlaces: Boolean) : Verdict
    data class Rejected(val fix: Fix, val reason: RejectReason) : Verdict
    data class Quarantined(val fix: Fix) : Verdict
    /** Карантин подтвердился: это была настоящая поездка. */
    data class Promoted(val fixes: List<Fix>) : Verdict
    /** Карантин не подтвердился: точки были ложными. */
    data class Dropped(val fixes: List<Fix>) : Verdict
}

data class FilterConfig(
    val maxAccuracyForPlacesM: Double = 150.0,
    val jitterFloorM: Double = 100.0,
    val noStepsJumpM: Double = 300.0,
    val stillJumpM: Double = 200.0,
    val metersPerStepMax: Double = 1.2,
    val maxWalkSpeedMs: Double = 150 / 3.6,
    val maxVehicleSpeedMs: Double = 300 / 3.6,
    val networkDisagreeM: Double = 1_000.0,
    val networkFreshMs: Long = 5 * MINUTE,
    val quarantineConfirmMs: Long = 30 * MINUTE,
    val quarantineClusterM: Double = 2_000.0,
    val quarantineExpireMs: Long = 12 * HOUR,
    val spoofZoneRadiusM: Double = 1_500.0,
    val spoofZoneHits: Int = 2,
    val gnssSkewLimitMs: Long = 10_000,
    val cn0FlatStdDevDb: Double = 2.0,
    val cn0FlatMinMeanDb: Double = 38.0,
    val cn0FlatMinSats: Int = 6,
)

/** Сохраняемое состояние фильтра. */
data class FilterState(
    val last: Fix? = null,
    val quarantine: List<Fix> = emptyList(),
    val quarantineSteps: Int = 0,
    val quarantineVehicle: Boolean = false,
    val spoofCandidates: List<Pair<LatLon, Int>> = emptyList(),
    val spoofZones: List<LatLon> = emptyList(),
)

/**
 * Фильтр правдоподобия. Опора: телефон всегда с владельцем, поэтому шаги и датчики
 * движения — независимый свидетель того, двигался человек или нет.
 */
class LocationFilter(
    var state: FilterState = FilterState(),
    private val cfg: FilterConfig = FilterConfig(),
) {

    /**
     * @param recentNetwork последняя сетевая точка (вышки/Wi-Fi) для перекрёстной проверки GPS.
     */
    fun process(fix: Fix, ctx: MotionContext, recentNetwork: Fix? = null): List<Verdict> {
        if (fix.isMock) return listOf(Verdict.Rejected(fix, RejectReason.MOCK))
        if (state.spoofZones.any { distanceMeters(it, fix.point) <= cfg.spoofZoneRadiusM }) {
            return listOf(Verdict.Rejected(fix, RejectReason.SPOOF_ZONE))
        }

        val last = state.last
        val satellite = fix.provider == Provider.GPS || fix.provider == Provider.FUSED
        val farFromLast = last != null && distanceMeters(last.point, fix.point) > tolerance(last, fix)

        if (satellite && farFromLast && gnssSuspicious(fix)) {
            return listOf(Verdict.Rejected(fix, RejectReason.GNSS_SIGNAL))
        }
        if (satellite && recentNetwork != null &&
            abs(fix.time - recentNetwork.time) <= cfg.networkFreshMs &&
            distanceMeters(recentNetwork.point, fix.point) > cfg.networkDisagreeM
        ) {
            return listOf(Verdict.Rejected(fix, RejectReason.NETWORK_DISAGREES))
        }

        if (state.quarantine.isNotEmpty()) return handleWithQuarantine(fix, ctx, recentNetwork)

        if (last == null) return accept(fix)
        val d = distanceMeters(last.point, fix.point)
        if (d <= tolerance(last, fix)) return accept(fix)

        if (ctx.motion == Motion.STILL && !ctx.vehicleSinceLast && ctx.stepsSinceLast < 30 && d > cfg.stillJumpM &&
            d <= cfg.noStepsJumpM
        ) {
            return listOf(Verdict.Rejected(fix, RejectReason.STILL_JUMP))
        }
        if (plausible(last, fix, ctx, d)) return accept(fix)

        state = state.copy(
            quarantine = listOf(fix),
            quarantineSteps = 0,
            quarantineVehicle = ctx.vehicleSinceLast,
        )
        return listOf(Verdict.Quarantined(fix))
    }

    /** Дополнительные шаги/транспорт, пока точка в карантине (вызывать между точками необязательно). */
    private fun handleWithQuarantine(fix: Fix, ctx: MotionContext, recentNetwork: Fix?): List<Verdict> {
        val q = state.quarantine
        val last = state.last
        val qCenter = weightedCenter(q.map { it.point to it.accuracyM })
        val steps = state.quarantineSteps + ctx.stepsSinceLast
        val vehicle = state.quarantineVehicle || ctx.vehicleSinceLast

        // Вернулись туда, где были: карантин был ложным.
        if (last != null && distanceMeters(last.point, fix.point) <= tolerance(last, fix)) {
            rememberSpoof(qCenter)
            state = state.copy(quarantine = emptyList(), quarantineSteps = 0, quarantineVehicle = false)
            return listOf(Verdict.Dropped(q)) + accept(fix)
        }

        if (fix.time - q.first().time > cfg.quarantineExpireMs) {
            state = state.copy(quarantine = listOf(fix), quarantineSteps = 0, quarantineVehicle = ctx.vehicleSinceLast)
            return listOf(Verdict.Dropped(q), Verdict.Quarantined(fix))
        }

        if (distanceMeters(qCenter, fix.point) > cfg.quarantineClusterM) {
            state = state.copy(quarantine = listOf(fix), quarantineSteps = 0, quarantineVehicle = ctx.vehicleSinceLast)
            return listOf(Verdict.Dropped(q), Verdict.Quarantined(fix))
        }

        val cluster = q + fix
        val span = fix.time - cluster.first().time
        val networkAgrees = cluster.any { it.provider == Provider.NETWORK } ||
            (recentNetwork != null && distanceMeters(recentNetwork.point, qCenter) <= cfg.networkDisagreeM)
        val networkContradicts = recentNetwork != null &&
            abs(recentNetwork.time - fix.time) <= cfg.networkFreshMs &&
            last != null && distanceMeters(recentNetwork.point, last.point) <= cfg.networkDisagreeM

        if (span >= cfg.quarantineConfirmMs && !networkContradicts && (networkAgrees || vehicle)) {
            state = state.copy(
                last = cluster.filter { it.accuracyM <= cfg.maxAccuracyForPlacesM }.lastOrNull() ?: fix,
                quarantine = emptyList(), quarantineSteps = 0, quarantineVehicle = false,
            )
            return listOf(Verdict.Promoted(cluster))
        }
        state = state.copy(quarantine = cluster, quarantineSteps = steps, quarantineVehicle = vehicle)
        return listOf(Verdict.Quarantined(fix))
    }

    private fun accept(fix: Fix): List<Verdict> {
        val usable = fix.accuracyM <= cfg.maxAccuracyForPlacesM
        val last = state.last
        if (usable || last == null) state = state.copy(last = fix)
        return listOf(Verdict.Accepted(fix, usable))
    }

    private fun tolerance(a: Fix, b: Fix): Double = maxOf(cfg.jitterFloorM, a.accuracyM + b.accuracyM)

    private fun plausible(last: Fix, fix: Fix, ctx: MotionContext, d: Double): Boolean {
        val dt = (fix.time - last.time).coerceAtLeast(1_000) / 1000.0
        val speed = d / dt
        if (ctx.vehicleSinceLast || ctx.motion == Motion.VEHICLE) return speed <= cfg.maxVehicleSpeedMs
        if (speed > cfg.maxWalkSpeedMs) return false
        if (d <= cfg.noStepsJumpM) return true
        if (ctx.motion == Motion.BICYCLE) return true
        val reachable = ctx.stepsSinceLast * cfg.metersPerStepMax + tolerance(last, fix)
        return d <= reachable
    }

    private fun gnssSuspicious(fix: Fix): Boolean {
        val skew = fix.gnssTimeSkewMs
        if (skew != null && abs(skew) > cfg.gnssSkewLimitMs) return true
        val sats = fix.satellites ?: return false
        val std = fix.cn0StdDevDb ?: return false
        val mean = fix.cn0MeanDb ?: return false
        // Один передатчик-подменщик даёт одинаковую «силу» всех спутников.
        return sats >= cfg.cn0FlatMinSats && std < cfg.cn0FlatStdDevDb && mean > cfg.cn0FlatMinMeanDb
    }

    private fun rememberSpoof(center: LatLon) {
        val updated = state.spoofCandidates.toMutableList()
        val i = updated.indexOfFirst { distanceMeters(it.first, center) <= cfg.spoofZoneRadiusM }
        if (i >= 0) updated[i] = updated[i].first to updated[i].second + 1 else updated.add(center to 1)
        val zones = state.spoofZones.toMutableList()
        updated.filter { it.second >= cfg.spoofZoneHits }.forEach { (c, _) ->
            if (zones.none { distanceMeters(it, c) <= cfg.spoofZoneRadiusM }) zones.add(c)
        }
        state = state.copy(spoofCandidates = updated.filter { it.second < cfg.spoofZoneHits }, spoofZones = zones)
    }
}
