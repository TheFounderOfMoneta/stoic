package app.ritm.core

import app.ritm.core.day.DayEngine
import app.ritm.core.time.HOUR
import app.ritm.core.time.Interval
import java.time.ZoneId
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class DayScenariosTest {
    private val engine = DayEngine()
    private val msk: (Long) -> ZoneId = { MSK }

    @Test fun `день — от пробуждения до засыпания, дата по пробуждению`() {
        val sleeps = listOf(Interval(t(0, 1, 40), t(0, 8, 10)), Interval(t(1, 0, 30), t(1, 7, 50)))
        val tl = engine.build(sleeps, t(0, 0), t(1, 12), msk)
        val d = tl.days.first { it.start == t(0, 8, 10) }
        assertEquals(BASE, d.date)
        assertEquals(t(1, 0, 30), d.end)
        val today = tl.current!!
        assertEquals(t(1, 7, 50), today.start)
        assertEquals(BASE.plusDays(1), today.date)
    }

    @Test fun `еда в 01_00 до сна — ещё вчерашний день`() {
        val sleeps = listOf(Interval(t(0, 0), t(0, 8)), Interval(t(1, 2), t(1, 9)))
        val tl = engine.build(sleeps, t(0, 0), t(1, 12), msk)
        val meal = t(1, 1)
        val day = tl.days.first { meal >= it.start && meal < (it.end ?: Long.MAX_VALUE) }
        assertEquals(BASE, day.date)
    }

    @Test fun `ночь без сна — день закрывается в обычное время пробуждения`() {
        // Проснулся в 08:00, не спал до вторника 13:00 (дневной сон не основной).
        val sleeps = listOf(Interval(t(0, 0), t(0, 8)))
        val tl = engine.build(sleeps, t(0, 0), t(1, 12), msk)
        val first = tl.days.first { it.start == t(0, 8) }
        assertEquals(t(1, 8), first.end)
        assertTrue(first.noSleep)
        assertEquals(t(1, 8), tl.current!!.start)
    }

    @Test fun `жёсткий предел 30 часов`() {
        // Обычно встаёт в 07:00, сегодня проснулся в 13:00 и не спит.
        val sleeps = (-5..-1).map { Interval(t(it, 0), t(it, 7)) } + Interval(t(0, 4), t(0, 13))
        val tl = engine.build(sleeps, t(-5, 0), t(2, 0), msk)
        val d = tl.days.first { it.start == t(0, 13) }
        assertEquals(t(0, 13) + 30 * HOUR, d.end)
    }

    @Test fun `два сна в одну ночь — одна граница дня`() {
        val sleeps = listOf(Interval(t(0, 0), t(0, 8)), Interval(t(0, 23), t(1, 1)), Interval(t(1, 3, 30), t(1, 8)))
        val tl = engine.build(sleeps, t(0, 0), t(1, 12), msk)
        val d = tl.days.first { it.start == t(0, 8) }
        assertEquals(t(0, 23), d.end)
        assertEquals(t(1, 8), tl.current!!.start)
    }

    @Test fun `сейчас идёт сон`() {
        val sleeps = listOf(Interval(t(0, 0), t(0, 8)), Interval(t(0, 23, 30), t(1, 4)))
        val tl = engine.build(sleeps, t(0, 0), t(1, 4), msk)
        assertNotNull(tl.sleepingSince)
        assertNull(tl.current)
    }

    @Test fun `перелёт во Владивосток — дата по местному времени пробуждения`() {
        val vlad = ZoneId.of("Asia/Vladivostok")
        val wake = t(1, 7, zone = vlad) // 07:00 по Владивостоку = 00:00 по Москве
        val sleeps = listOf(Interval(t(0, 0), t(0, 8)), Interval(wake - 6 * HOUR, wake))
        val tl = engine.build(sleeps, t(0, 0), wake + HOUR, { at -> if (at >= wake - 6 * HOUR) vlad else MSK })
        assertEquals(BASE.plusDays(1), tl.current!!.date)
    }
}
