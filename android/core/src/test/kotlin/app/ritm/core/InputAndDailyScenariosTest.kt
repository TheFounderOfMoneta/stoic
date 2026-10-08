package app.ritm.core

import app.ritm.core.day.NowItem
import app.ritm.core.day.nowLine
import app.ritm.core.food.FoodHit
import app.ritm.core.food.shouldRemindFood
import app.ritm.core.food.suggestCombos
import app.ritm.core.food.suggestFood
import app.ritm.core.food.weightCardVisible
import app.ritm.core.input.Gesture
import app.ritm.core.input.PressGestureDetector
import app.ritm.core.input.SingleAction
import app.ritm.core.input.isVoiceNoteValid
import app.ritm.core.input.shouldIgnorePress
import app.ritm.core.input.singlePressAction
import app.ritm.core.steps.StepCounterTracker
import app.ritm.core.steps.StepInterval
import app.ritm.core.steps.stepsIn
import app.ritm.core.tasks.TaskItem
import app.ritm.core.tasks.carryOver
import app.ritm.core.time.HOUR
import app.ritm.core.time.Interval
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

class InputAndDailyScenariosTest {

    private fun run(d: PressGestureDetector, script: List<Pair<Long, Boolean?>>): List<Gesture> {
        val out = mutableListOf<Gesture>()
        for ((t, down) in script) {
            out += d.onTick(t)
            out += when (down) { true -> d.onDown(t); false -> d.onUp(t); null -> emptyList() }
        }
        return out
    }

    @Test fun `кнопка — одно, два, три нажатия и удержание`() {
        assertEquals(listOf(Gesture.Taps(1)), run(PressGestureDetector(), listOf(0L to true, 100L to false, 500L to null)))
        assertEquals(listOf(Gesture.Taps(2)), run(PressGestureDetector(), listOf(0L to true, 80L to false, 250L to true, 330L to false, 800L to null)))
        assertEquals(listOf(Gesture.Taps(3)), run(PressGestureDetector(), listOf(0L to true, 80L to false, 200L to true, 280L to false, 400L to true, 470L to false)))
        val hold = run(PressGestureDetector(), listOf(0L to true, 460L to null, 3000L to false))
        assertEquals(listOf(Gesture.HoldStart, Gesture.HoldEnd(3000)), hold)
    }

    @Test fun `нажал и сразу зажал — это удержание`() {
        val g = run(PressGestureDetector(), listOf(0L to true, 80L to false, 200L to true, 700L to null, 2200L to false))
        assertEquals(listOf(Gesture.HoldStart, Gesture.HoldEnd(2000)), g)
    }

    @Test fun `следующий дедлайн для таймера`() {
        val d = PressGestureDetector()
        d.onDown(0); assertEquals(450L, d.nextDeadline())
        d.onUp(100); assertEquals(450L, d.nextDeadline())
        d.onTick(450); assertNull(d.nextDeadline())
    }

    @Test fun `карман — игнор, кроме тренировки`() {
        assertTrue(shouldIgnorePress(screenOn = false, proximityNear = true, workoutActive = false))
        assertTrue(!shouldIgnorePress(screenOn = false, proximityNear = true, workoutActive = true))
        assertTrue(!shouldIgnorePress(screenOn = true, proximityNear = true, workoutActive = false))
    }

    @Test fun `одно нажатие — тренировка, потом вес, иначе плюс`() {
        assertEquals(SingleAction.WORKOUT_SET, singlePressAction(workoutActive = true, weightCardVisible = true))
        assertEquals(SingleAction.WEIGHT, singlePressAction(workoutActive = false, weightCardVisible = true))
        assertEquals(SingleAction.PLUS, singlePressAction(workoutActive = false, weightCardVisible = false))
    }

    @Test fun `удержание короче полсекунды — не заметка`() {
        assertTrue(!isVoiceNoteValid(300)); assertTrue(isVoiceNoteValid(800))
    }

    @Test fun `строка сверху — по приоритету`() {
        assertEquals(NowItem.WORKOUT, nowLine(true, true, true, true, true))
        assertEquals(NowItem.WEIGHT, nowLine(false, false, true, true, true))
        assertEquals(NowItem.SLEEP_UNSURE, nowLine(false, false, false, false, true))
        assertNull(nowLine(false, false, false, false, false))
    }

    @Test fun `карточка веса — 4 часа после пробуждения, даже если встал в 14_00`() {
        val wake = t(0, 14)
        assertTrue(weightCardVisible(wake + HOUR, wake, weighedToday = false, dismissed = false))
        assertTrue(!weightCardVisible(wake + 5 * HOUR, wake, weighedToday = false, dismissed = false))
        assertTrue(!weightCardVisible(wake + HOUR, wake, weighedToday = true, dismissed = false))
    }

    @Test fun `обычно сейчас — утром завтрак, вечером ужин`() {
        val hist = (1..10).flatMap { d ->
            listOf(FoodHit("овсянка", t(-d, 8, 30), 30), FoodHit("гречка", t(-d, 19), 660))
        }
        assertEquals("овсянка", suggestFood(hist, t(0, 8, 20), 20).first())
        assertEquals("гречка", suggestFood(hist, t(0, 19), 650).first())
    }

    @Test fun `одинаковый завтрак 3 раза — предложить комплект`() {
        val hist = (1..3).flatMap { d ->
            listOf(FoodHit("яйца", t(-d, 8), 10), FoodHit("хлеб", t(-d, 8, 5), 15), FoodHit("кофе", t(-d, 8, 10), 20))
        }
        assertEquals(listOf(setOf("яйца", "хлеб", "кофе")), suggestCombos(hist, t(0, 12), emptySet()))
        assertTrue(suggestCombos(hist, t(0, 12), setOf(setOf("яйца", "хлеб", "кофе"))).isEmpty())
    }

    @Test fun `напоминание о еде — по своему шаблону от пробуждения, один раз`() {
        val wake = t(0, 13) // проснулся поздно
        val usual = listOf(240, 250, 230, 260, 245) // обычно ест через ~4 ч
        assertTrue(!shouldRemindFood(wake + 4 * HOUR, wake, usual, false, false, false))
        assertTrue(shouldRemindFood(wake + 5 * HOUR + 10 * 60_000, wake, usual, false, false, false))
        assertTrue(!shouldRemindFood(wake + 6 * HOUR, wake, usual, false, skipToday = true, remindedToday = false))
        assertTrue(!shouldRemindFood(wake + 6 * HOUR, wake, usual, false, false, remindedToday = true))
        assertTrue(!shouldRemindFood(wake + 6 * HOUR, wake, usual.take(3), false, false, false))
    }

    @Test fun `невыполненные задачи переносятся, счётчик растёт`() {
        val today = BASE.plusDays(2)
        val out = carryOver(listOf(TaskItem(1, BASE, false, 1), TaskItem(2, BASE, true), TaskItem(3, null, false)), today)
        assertEquals(today, out[0].date); assertEquals(2, out[0].carryCount)
        assertEquals(BASE, out[1].date)
        assertNull(out[2].date)
    }

    @Test fun `шаги — обычный прирост и перезагрузка`() {
        val tr = StepCounterTracker()
        assertNull(tr.onReading(1000, t(0, 10), t(-1, 0)))
        assertEquals(StepInterval(Interval(t(0, 10), t(0, 10, 15)), 500), tr.onReading(1500, t(0, 10, 15), t(-1, 0)))
        val boot = t(0, 11)
        assertEquals(StepInterval(Interval(boot, t(0, 11, 30)), 300), tr.onReading(300, t(0, 11, 30), boot))
        assertNull(tr.onReading(300, t(0, 11, 45), boot))
    }

    @Test fun `шаги делятся пропорционально по интервалам`() {
        val s = listOf(StepInterval(Interval(t(0, 10), t(0, 12)), 1200))
        assertEquals(600, stepsIn(Interval(t(0, 11), t(0, 13)), s))
    }
}
