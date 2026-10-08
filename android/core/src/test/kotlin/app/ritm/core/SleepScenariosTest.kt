package app.ritm.core

import app.ritm.core.sleep.Confidence
import app.ritm.core.sleep.SleepDetector
import app.ritm.core.sleep.SleepSignals
import app.ritm.core.steps.StepInterval
import app.ritm.core.time.Interval
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class SleepScenariosTest {
    private val detector = SleepDetector()

    private fun evening() = pings(t(0, 21), t(0, 23, 40))
    private fun morning() = pings(t(1, 8, 10), t(1, 9))

    @Test fun `обычная ночь — сон 23_40–08_10`() {
        val s = detector.detect(SleepSignals(evening() + morning()), t(1, 9), MSK)
        assertEquals(1, s.size)
        assertEquals(Interval(t(0, 23, 40), t(1, 8, 10)), s[0].interval)
        assertTrue(s[0].night)
        assertTrue(!s[0].needsConfirmation)
    }

    @Test fun `касание до 5 минут ночью сон не прерывает`() {
        val s = detector.detect(SleepSignals(evening() + pings(t(1, 3), t(1, 3, 3)) + morning()), t(1, 9), MSK)
        assertEquals(1, s.size)
        assertEquals(1, s[0].interruptions)
        assertEquals(t(0, 23, 40), s[0].interval.start)
    }

    @Test fun `ночное пробуждение 25 минут склеивается в один сон`() {
        val s = detector.detect(SleepSignals(evening() + pings(t(1, 1, 40), t(1, 2, 5)) + morning()), t(1, 9), MSK)
        assertEquals(1, s.size)
        assertEquals(Interval(t(0, 23, 40), t(1, 8, 10)), s[0].interval)
    }

    @Test fun `пробуждение больше 45 минут ночью разрывает сон`() {
        val s = detector.detect(SleepSignals(evening() + pings(t(1, 2), t(1, 3)) + morning()), t(1, 9), MSK)
        assertEquals(2, s.size)
    }

    @Test fun `переписка раз в 10 минут — не сон`() {
        val chat = (0 until 18).flatMap { i -> pings(t(0, 12) + i * 600_000L, t(0, 12) + i * 600_000L + 60_000) }
        val s = detector.detect(SleepSignals(chat + pings(t(0, 15, 5), t(0, 16))), t(0, 16), MSK)
        assertTrue(s.isEmpty())
    }

    @Test fun `полежал без телефона 1 ч 50 мин — не сон`() {
        val s = detector.detect(SleepSignals(pings(t(0, 12), t(0, 13)) + pings(t(0, 14, 50), t(0, 15))), t(0, 15), MSK)
        assertTrue(s.isEmpty())
    }

    @Test fun `дневной сон требует подтверждения`() {
        val s = detector.detect(SleepSignals(pings(t(0, 12), t(0, 14)) + pings(t(0, 16, 30), t(0, 17))), t(0, 17), MSK)
        assertEquals(1, s.size)
        assertTrue(!s[0].night)
        assertTrue(s[0].needsConfirmation)
    }

    @Test fun `ходил 2,5 часа не трогая телефон — не сон`() {
        val steps = listOf(StepInterval(Interval(t(0, 12, 30), t(0, 14, 30)), 4000))
        val s = detector.detect(SleepSignals(pings(t(0, 11), t(0, 12)) + pings(t(0, 14, 40), t(0, 15)), steps = steps), t(0, 15), MSK)
        assertTrue(s.isEmpty())
    }

    @Test fun `уверенность — высокая, средняя, низкая`() {
        val night = Interval(t(0, 22), t(1, 10))
        val act = evening() + morning()
        val high = detector.detect(SleepSignals(act, charging = listOf(night), atHome = listOf(night), pcCovered = listOf(night)), t(1, 9), MSK)
        assertEquals(Confidence.HIGH, high[0].confidence)
        val noPc = detector.detect(SleepSignals(act, charging = listOf(night), atHome = listOf(night)), t(1, 9), MSK)
        assertEquals(Confidence.MEDIUM, noPc[0].confidence)
        val away = detector.detect(SleepSignals(act), t(1, 9), MSK)
        assertEquals(Confidence.LOW, away[0].confidence)
    }

    @Test fun `сон ещё идёт`() {
        val s = detector.detect(SleepSignals(evening()), t(1, 4), MSK)
        assertEquals(1, s.size)
        assertTrue(s[0].ongoing)
    }
}
