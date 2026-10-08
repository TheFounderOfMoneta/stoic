package app.ritm.core

import app.ritm.core.geo.Fix
import app.ritm.core.geo.LatLon
import app.ritm.core.geo.LocationFilter
import app.ritm.core.geo.Motion
import app.ritm.core.geo.MotionContext
import app.ritm.core.geo.Provider
import app.ritm.core.geo.RejectReason
import app.ritm.core.geo.Verdict
import app.ritm.core.geo.distanceMeters
import app.ritm.core.geo.weightedCenter
import app.ritm.core.time.HOUR
import app.ritm.core.time.MINUTE
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertTrue

class GeoScenariosTest {
    private val home = LatLon(55.7520, 37.6175)       // центр Москвы
    private val vnukovo = LatLon(55.5915, 37.2615)    // аэропорт — типичная подмена
    private val sochi = LatLon(43.5855, 39.7231)

    private fun gps(time: Long, p: LatLon, acc: Double = 10.0) = Fix(time, p, acc, Provider.GPS)
    private fun net(time: Long, p: LatLon) = Fix(time, p, 60.0, Provider.NETWORK)
    private val still = MotionContext(0, Motion.STILL)

    private fun filterAtHome(): LocationFilter = LocationFilter().also { it.process(gps(t(0, 10), home), still) }

    @Test fun `дома, GPS показывает Внуково, сеть показывает дом — точка отброшена`() {
        val f = filterAtHome()
        val v = f.process(gps(t(0, 10, 5), vnukovo), still, recentNetwork = net(t(0, 10, 4), home))
        assertEquals(RejectReason.NETWORK_DISAGREES, (v.single() as Verdict.Rejected).reason)
    }

    @Test fun `подмена без интернета — карантин, возврат домой, после двух раз — зона подмены`() {
        val f = filterAtHome()
        repeat(2) { i ->
            val base = t(0, 11 + i * 2)
            assertIs<Verdict.Quarantined>(f.process(gps(base, vnukovo), still).single())
            assertIs<Verdict.Quarantined>(f.process(gps(base + 10 * MINUTE, vnukovo), still).single())
            val back = f.process(gps(base + 40 * MINUTE, home), still)
            assertIs<Verdict.Dropped>(back[0])
            assertIs<Verdict.Accepted>(back[1])
        }
        assertEquals(1, f.state.spoofZones.size)
        val v = f.process(gps(t(0, 18), vnukovo), still)
        assertEquals(RejectReason.SPOOF_ZONE, (v.single() as Verdict.Rejected).reason)
    }

    @Test fun `подмена держится час, интернета нет — карантин не подтверждается`() {
        val f = filterAtHome()
        (0..6).forEach { i -> f.process(gps(t(0, 11) + i * 10 * MINUTE, vnukovo), still) }
        assertTrue(f.state.quarantine.isNotEmpty())
        assertEquals(home, f.state.last!!.point)
    }

    @Test fun `прогулка 400 м с шагами — принято`() {
        val f = filterAtHome()
        val p = LatLon(home.lat + 0.0036, home.lon) // ~400 м
        val v = f.process(gps(t(0, 10, 6), p), MotionContext(520, Motion.WALKING))
        assertIs<Verdict.Accepted>(v.single())
    }

    @Test fun `прыжок на 30 км без шагов и транспорта — карантин`() {
        val f = filterAtHome()
        assertIs<Verdict.Quarantined>(f.process(gps(t(0, 10, 3), vnukovo), MotionContext(3, Motion.UNKNOWN)).single())
    }

    @Test fun `перелёт в Сочи — карантин, 30 минут стабильно и сеть согласна — подтверждено`() {
        val f = filterAtHome()
        val landed = t(0, 14)
        val ctx = MotionContext(40, Motion.STILL, vehicleSinceLast = true)
        assertIs<Verdict.Quarantined>(f.process(gps(landed, sochi), ctx).single())
        f.process(gps(landed + 15 * MINUTE, sochi), MotionContext(200, Motion.WALKING))
        val v = f.process(gps(landed + 35 * MINUTE, sochi), MotionContext(150, Motion.WALKING), recentNetwork = net(landed + 34 * MINUTE, sochi))
        assertIs<Verdict.Promoted>(v.single())
        assertTrue(distanceMeters(f.state.last!!.point, sochi) < 100)
    }

    @Test fun `машина 20 км за 20 минут — принято`() {
        val f = filterAtHome()
        val p = LatLon(home.lat + 0.18, home.lon)
        assertIs<Verdict.Accepted>(f.process(gps(t(0, 10, 20), p), MotionContext(0, Motion.VEHICLE)).single())
    }

    @Test fun `скорость 900 км_ч даже в транспорте — карантин`() {
        val f = filterAtHome()
        val p = LatLon(home.lat + 1.35, home.lon) // ~150 км
        assertIs<Verdict.Quarantined>(f.process(gps(t(0, 10, 10), p), MotionContext(0, Motion.VEHICLE)).single())
    }

    @Test fun `точка от приложения-подделки — отброшена`() {
        val f = filterAtHome()
        val v = f.process(Fix(t(0, 10, 1), home, 5.0, Provider.GPS, isMock = true), still)
        assertEquals(RejectReason.MOCK, (v.single() as Verdict.Rejected).reason)
    }

    @Test fun `одинаковая сила всех спутников далеко от дома — подмена`() {
        val f = filterAtHome()
        val fix = Fix(t(0, 10, 2), vnukovo, 5.0, Provider.GPS, satellites = 9, cn0MeanDb = 45.0, cn0StdDevDb = 0.8)
        assertEquals(RejectReason.GNSS_SIGNAL, (f.process(fix, still).single() as Verdict.Rejected).reason)
    }

    @Test fun `время GNSS расходится на минуту — подмена`() {
        val f = filterAtHome()
        val fix = Fix(t(0, 10, 2), vnukovo, 5.0, Provider.GPS, gnssTimeSkewMs = 60_000)
        assertEquals(RejectReason.GNSS_SIGNAL, (f.process(fix, still).single() as Verdict.Rejected).reason)
    }

    @Test fun `точность 300 м — принято, но не для мест`() {
        val f = filterAtHome()
        val v = f.process(Fix(t(0, 10, 5), LatLon(home.lat + 0.001, home.lon), 300.0, Provider.NETWORK), still)
        assertTrue(!(v.single() as Verdict.Accepted).usableForPlaces)
        assertEquals(home, f.state.last!!.point)
    }

    @Test fun `дрожание в пределах точности — принято`() {
        val f = filterAtHome()
        val p = LatLon(home.lat + 0.0003, home.lon)
        assertIs<Verdict.Accepted>(f.process(gps(t(0, 10, 5), p), still).single())
    }

    @Test fun `стою, без шагов, точка уехала на 250 м — отброшена`() {
        val f = filterAtHome()
        val p = LatLon(home.lat + 0.00225, home.lon)
        assertEquals(RejectReason.STILL_JUMP, (f.process(gps(t(0, 10, 5), p), still).single() as Verdict.Rejected).reason)
    }

    @Test fun `центр стоянки — без выброса`() {
        val pts = (0 until 10).map { LatLon(home.lat + it * 0.00001, home.lon) to 15.0 } + (LatLon(home.lat + 0.01, home.lon) to 20.0)
        assertTrue(distanceMeters(weightedCenter(pts), home) < 20)
    }

    @Test fun `карантин истекает через 12 часов`() {
        val f = filterAtHome()
        f.process(gps(t(0, 11), vnukovo), still)
        val v = f.process(gps(t(0, 11) + 13 * HOUR, vnukovo), still)
        assertIs<Verdict.Dropped>(v[0])
    }
}
