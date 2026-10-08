package app.ritm.core

import app.ritm.core.energy.Body
import app.ritm.core.energy.EnergyDay
import app.ritm.core.energy.Energy
import app.ritm.core.energy.GoalType
import app.ritm.core.energy.Sex
import app.ritm.core.energy.WeightGoal
import app.ritm.core.energy.calibrate
import app.ritm.core.energy.cycleExpenditure
import app.ritm.core.energy.dailyPlan
import app.ritm.core.energy.weightTrend
import app.ritm.core.steps.StepInterval
import app.ritm.core.time.Interval
import java.time.LocalDate
import kotlin.random.Random
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class EnergyScenariosTest {
    private val man = Body(Sex.MALE, 180.0, LocalDate.of(1996, 1, 1))
    private val woman = Body(Sex.FEMALE, 165.0, LocalDate.of(2001, 1, 1))
    private val on = LocalDate.of(2026, 10, 8)

    @Test fun `базовый расход по Миффлину`() {
        assertEquals(1780.0, Energy.bmrPerDay(man, 80.0, on), 0.01)
        assertEquals(1345.25, Energy.bmrPerDay(woman, 60.0, on), 0.01)
    }

    @Test fun `10 000 шагов при 80 кг — около 300 ккал`() {
        assertEquals(298.8, Energy.stepsKcal(10_000, 80.0, man), 0.5)
    }

    @Test fun `цикл 26 часов — базовый пропорционально, шаги в зале не дублируются`() {
        val cycle = Interval(t(0, 8), t(1, 10))
        val gym = Interval(t(0, 18), t(0, 19))
        val steps = listOf(StepInterval(Interval(t(0, 9), t(0, 10)), 4000), StepInterval(gym, 1500))
        val e = cycleExpenditure(man, 80.0, cycle, on, steps, listOf(gym))
        assertEquals(1780.0 / 24 * 26, e.bmr, 0.5)
        assertEquals(Energy.stepsKcal(4000, 80.0, man), e.steps, 0.5)
        assertEquals(Energy.workoutKcal(60.0, 80.0), e.workouts, 0.5)
    }

    @Test fun `план без истории — низкая активность минус дефицит`() {
        val plan = dailyPlan(man, 80.0, on, WeightGoal(GoalType.LOSE, 0.5), averageActivity = null)
        assertEquals((1780 * 1.2 - 550).toInt(), plan)
    }

    @Test fun `план не опускается ниже безопасного минимума`() {
        val plan = dailyPlan(woman, 50.0, on, WeightGoal(GoalType.LOSE, 1.0), averageActivity = null)
        assertTrue(plan >= 1200)
    }

    @Test fun `набор — профицит`() {
        val plan = dailyPlan(man, 80.0, on, WeightGoal(GoalType.GAIN, 0.25), averageActivity = 300.0)
        assertEquals((1780 + 300 + 275.0).toInt(), plan)
    }

    private fun simulate(days: Int, weighEvery: Int = 1): Pair<List<EnergyDay>, List<Pair<LocalDate, Double>>> {
        val rnd = Random(42)
        val realTdee = 2500.0
        var w = 85.0
        val start = on.minusDays(days.toLong())
        val ed = mutableListOf<EnergyDay>()
        val ws = mutableListOf<Pair<LocalDate, Double>>()
        for (i in 0 until days) {
            val d = start.plusDays(i.toLong())
            val forgot = i % 9 == 4
            val intake = 2000.0
            ed += EnergyDay(d, if (forgot) 600.0 else intake, 2200.0, complete = !forgot)
            w -= (realTdee - intake) / Energy.KCAL_PER_KG
            if (i % weighEvery == 0) ws += d to (w + rnd.nextDouble(-0.6, 0.6))
        }
        return ed to ws
    }

    @Test fun `калибровка находит реальный расход и игнорирует неполные дни`() {
        val (ed, ws) = simulate(35)
        val c = assertNotNull(calibrate(ed, ws, on))
        assertTrue(c.factor in 1.07..1.21, "factor=${c.factor}")
        assertTrue(c.realTdee in 2350.0..2650.0, "real=${c.realTdee}")
    }

    @Test fun `калибровка не раньше трёх недель`() {
        val (ed, ws) = simulate(18)
        assertNull(calibrate(ed, ws, on))
    }

    @Test fun `калибровка требует минимум 10 взвешиваний`() {
        val (ed, ws) = simulate(35, weighEvery = 4)
        assertNull(calibrate(ed, ws, on))
    }

    @Test fun `тренд веса сглаживает скачки воды`() {
        val ws = listOf(on to 80.0, on.plusDays(1) to 81.5, on.plusDays(2) to 79.8)
        val tr = weightTrend(ws)
        assertTrue(tr.getValue(on.plusDays(1)) < 80.3)
    }
}
