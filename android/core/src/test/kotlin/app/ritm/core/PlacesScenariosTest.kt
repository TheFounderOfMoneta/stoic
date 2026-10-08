package app.ritm.core

import app.ritm.core.geo.FingerprintBook
import app.ritm.core.geo.LatLon
import app.ritm.core.geo.RadioScan
import app.ritm.core.places.Place
import app.ritm.core.places.PlaceKind
import app.ritm.core.places.Stay
import app.ritm.core.places.StayDetector
import app.ritm.core.places.StayEvent
import app.ritm.core.places.guessPlaceKind
import app.ritm.core.places.placeAt
import app.ritm.core.places.shouldSuggestPlace
import app.ritm.core.time.Interval
import app.ritm.core.time.MINUTE
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

class PlacesScenariosTest {
    private val gym = LatLon(55.7000, 37.6000)
    private fun near(p: LatLon, dLatM: Double) = LatLon(p.lat + dLatM / 111_000, p.lon)

    @Test fun `больше часа в радиусе 200 м — предложение сохранить, один раз`() {
        val d = StayDetector()
        val events = mutableListOf<StayEvent>()
        for (i in 0..14) events += d.onFix(t(0, 18) + i * 5 * MINUTE, near(gym, (i % 3) * 30.0), 20.0)
        assertEquals(1, events.count { it is StayEvent.LongStay })
    }

    @Test fun `вышел на 8 минут и вернулся — стоянка не сбрасывается`() {
        val d = StayDetector()
        val ev = mutableListOf<StayEvent>()
        ev += d.onFix(t(0, 18), gym, 15.0)
        ev += d.onFix(t(0, 18, 30), gym, 15.0)
        ev += d.onFix(t(0, 18, 35), near(gym, 400.0), 15.0)
        ev += d.onFix(t(0, 18, 43), gym, 15.0)
        ev += d.onFix(t(0, 19, 1), gym, 15.0)
        assertTrue(ev.any { it is StayEvent.LongStay && it.start == t(0, 18) })
    }

    @Test fun `ушёл больше чем на 10 минут — стоянка закончилась`() {
        val d = StayDetector()
        d.onFix(t(0, 18), gym, 15.0)
        d.onFix(t(0, 19, 10), gym, 15.0)
        d.onFix(t(0, 19, 15), near(gym, 2000.0), 15.0)
        val ev = d.onFix(t(0, 19, 30), near(gym, 2100.0), 15.0)
        val ended = ev.filterIsInstance<StayEvent.Ended>().single()
        assertEquals(Interval(t(0, 18), t(0, 19, 10)), ended.stay.interval)
    }

    @Test fun `стоим на месте без точек — отметки продлевают стоянку`() {
        val d = StayDetector()
        d.onFix(t(0, 9), gym, 15.0)
        val ev = (1..14).flatMap { d.onTick(t(0, 9) + it * 5 * MINUTE, 3, still = true) }
        assertIs<StayEvent.LongStay>(ev.single())
    }

    @Test fun `внутри своего места и рядом с отклонённым — не предлагать`() {
        val places = listOf(Place(1, "Зал", gym))
        assertTrue(!shouldSuggestPlace(near(gym, 50.0), places, emptyList()))
        val other = LatLon(55.8, 37.7)
        assertTrue(!shouldSuggestPlace(other, places, listOf(near(other, 80.0))))
        assertTrue(shouldSuggestPlace(other, places, emptyList()))
    }

    @Test fun `пересекающиеся места — ближайшее по центру`() {
        val a = Place(1, "Дом", gym)
        val b = Place(2, "Зал", near(gym, 250.0))
        assertEquals(2, placeAt(near(gym, 180.0), listOf(a, b))!!.id)
    }

    @Test fun `угадывание названия — дом, работа, зал`() {
        val home = LatLon(55.75, 37.61)
        assertEquals(PlaceKind.HOME, guessPlaceKind(home, listOf(Stay(Interval(t(0, 23), t(1, 8)), home)), MSK))
        val work = LatLon(55.76, 37.62)
        val ws = listOf(Stay(Interval(t(0, 9), t(0, 18)), work), Stay(Interval(t(1, 9, 30), t(1, 18)), work))
        assertEquals(PlaceKind.WORK, guessPlaceKind(work, ws, MSK))
        val gs = listOf(Stay(Interval(t(0, 19), t(0, 20, 30)), gym), Stay(Interval(t(2, 19), t(2, 20, 15)), gym))
        assertEquals(PlaceKind.GYM, guessPlaceKind(gym, gs, MSK))
        assertNull(guessPlaceKind(gym, gs.take(1), MSK))
    }

    @Test fun `раздача телефона в разных местах — не учитывается, дом узнаётся по роутеру`() {
        val book = FingerprintBook()
        val home = LatLon(55.75, 37.61)
        val work = LatLon(55.80, 37.70)
        repeat(3) { book.learn(1, RadioScan(0, setOf("router-home", "neighbour"), setOf("cell-1"), connectedBssid = "router-home", location = home)) }
        book.learn(1, RadioScan(0, setOf("router-home", "phone-hotspot"), setOf("cell-1"), location = home))
        book.observe(RadioScan(0, setOf("phone-hotspot"), setOf("cell-9"), location = work))
        assertTrue("phone-hotspot" in book.mobileWifi)
        assertEquals(1L, book.match(RadioScan(0, setOf("router-home"), emptySet(), connectedBssid = "router-home")))
        assertNull(book.match(RadioScan(0, setOf("phone-hotspot"), emptySet(), connectedBssid = "phone-hotspot")))
    }

    @Test fun `точка доступа телефона по признаку Android — сразу исключается`() {
        val book = FingerprintBook()
        book.learn(1, RadioScan(0, setOf("hs"), emptySet(), connectedBssid = "hs", connectedIsHotspot = true, location = gym))
        assertTrue("hs" in book.mobileWifi)
    }

    @Test fun `без GPS и интернета — место по вышкам`() {
        val book = FingerprintBook()
        repeat(4) { book.learn(7, RadioScan(0, emptySet(), setOf("250-1-100-5", "250-1-100-6"), location = gym)) }
        assertEquals(7L, book.match(RadioScan(0, emptySet(), setOf("250-1-100-5", "250-1-100-6"))))
    }
}
