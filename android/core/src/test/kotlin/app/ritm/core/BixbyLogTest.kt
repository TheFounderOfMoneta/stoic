package app.ritm.core

import app.ritm.core.input.BixbyLogDecoder
import app.ritm.core.input.BixbyLogDecoder.Edge
import app.ritm.core.input.Gesture
import app.ritm.core.input.PressGestureDetector
import app.ritm.core.input.parseBixbyLine
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Реальные строки журнала с S10+ (выборка из теста рисков, 08.10.2026). */
class BixbyLogTest {
    private fun line(i: Boolean) =
        "getIntentBixbyService, keyPressType=-1 interactive=$i isUnlockFP=false longPress=false doublePress=false isPowerKeyCombination=false"

    private fun ms(s: String) = (s.toDouble() * 1000).toLong()

    private fun decode(seq: List<Pair<String, Boolean>>): List<Pair<Long, Edge?>> {
        val d = BixbyLogDecoder()
        return seq.map { (t, i) -> ms(t) to d.onLine(ms(t), parseBixbyLine(line(i))) }
    }

    /** Жесты по декодированным краям (время в мс эпохи, детектор работает в любой единой шкале). */
    private fun gestures(edges: List<Pair<Long, Edge?>>): List<Gesture> {
        val g = PressGestureDetector()
        val out = mutableListOf<Gesture>()
        for ((t, e) in edges) {
            out += g.onTick(t)
            when (e) { Edge.DOWN -> out += g.onDown(t); Edge.UP -> out += g.onUp(t); null -> {} }
        }
        out += g.onTick(edges.last().first + 2_000)
        return out
    }

    @Test fun `строка про кнопку распознаётся, чужие — нет`() {
        assertEquals(true, parseBixbyLine(line(true)))
        assertEquals(false, parseBixbyLine(line(false)))
        assertNull(parseBixbyLine("Unable to start service Intent { cmp=com.samsung.android.bixby.agent/com.samsung.android.bixby.WinkService (has extras) } U=0: not found"))
        assertNull(parseBixbyLine("[1,PhoneWindowManager.mBixbyServiceWakeLock:android]"))
    }

    @Test fun `блокировка, быстрая серия — строки чередуются нажал-отпустил, нажатия 0,15–0,19 с`() {
        val seq = listOf("1791463014.837", "1791463015.028", "1791463015.543", "1791463015.688", "1791463016.064", "1791463016.236",
            "1791463016.591", "1791463016.751").map { it to true }
        val e = decode(seq)
        assertEquals(listOf(Edge.DOWN, Edge.UP, Edge.DOWN, Edge.UP, Edge.DOWN, Edge.UP, Edge.DOWN, Edge.UP), e.map { it.second })
        e.chunked(2).forEach { (d, u) -> assertTrue(u.first - d.first in 140..200) }
    }

    @Test fun `включённый экран — служебная строка через 400 мс отбрасывается`() {
        // нажал 1.317, отпустил 1.979, таймер 2.378 (interactive=false), нажал 3.000, отпустил 3.567, таймер 3.966
        val seq = listOf("1791462912.317" to true, "1791462912.979" to true, "1791462913.378" to false,
            "1791462914.000" to true, "1791462914.567" to true, "1791462914.966" to false)
        assertEquals(listOf(Edge.DOWN, Edge.UP, null, Edge.DOWN, Edge.UP, null), decode(seq).map { it.second })
    }

    @Test fun `выключенный экран — всё interactive=false, таймер узнаётся по 400 мс`() {
        // нажал 172.475, отпустил 172.974, таймер 173.373; нажал 173.977, отпустил 174.474, таймер 174.873
        val seq = listOf("1791463172.475", "1791463172.974", "1791463173.373", "1791463173.977", "1791463174.474", "1791463174.873").map { it to false }
        assertEquals(listOf(Edge.DOWN, Edge.UP, null, Edge.DOWN, Edge.UP, null), decode(seq).map { it.second })
    }

    @Test fun `выключенный экран, быстрые нажатия — без таймера, пары по 0,1–0,2 с`() {
        val seq = listOf("1791463018.366", "1791463018.712", "1791463018.887", "1791463019.325", "1791463019.488").map { it to false }
        // 018.366 — отпускание нажатия, начатого в предыдущей строке (018.203, экран ещё был включён)
        val d = BixbyLogDecoder()
        d.onLine(ms("1791463018.203"), true)
        val edges = seq.map { (t, i) -> d.onLine(ms(t), i) }
        assertEquals(listOf(Edge.UP, Edge.DOWN, Edge.UP, Edge.DOWN, Edge.UP), edges)
    }

    @Test fun `жесты из журнала — одно, два, три нажатия и удержание`() {
        fun press(start: Long, hold: Long) = listOf(start to Edge.DOWN, start + hold to Edge.UP)
        val single = press(0, 150)
        assertEquals(listOf<Gesture>(Gesture.Taps(1)), gestures(single))
        val double = press(0, 150) + press(330, 140)
        assertEquals(listOf<Gesture>(Gesture.Taps(2)), gestures(double))
        val triple = press(0, 120) + press(280, 120) + press(560, 120)
        assertEquals(listOf<Gesture>(Gesture.Taps(3)), gestures(triple))
        val hold = press(0, 1_600)
        assertEquals(listOf(Gesture.HoldStart, Gesture.HoldEnd(1_600)), gestures(hold))
    }

    @Test fun `реальная серия удержаний на выключенном экране даёт удержания`() {
        // getevent OFF: 0,8–1,0 с удержания; журнал — нажал/отпустил + таймер
        val seq = listOf("1791463172.475" to false, "1791463172.974" to false, "1791463173.373" to false)
        val g = gestures(decode(seq))
        assertEquals(listOf(Gesture.HoldStart, Gesture.HoldEnd(499)), g)
    }

    @Test fun `застрявшее нажатие сбрасывается`() {
        val d = BixbyLogDecoder(stuckMs = 5_000)
        assertEquals(Edge.DOWN, d.onLine(0, true))
        assertEquals(Edge.DOWN, d.onLine(10_000, true))
    }
}
