package app.ritm.core.energy

import app.ritm.core.steps.StepInterval
import app.ritm.core.steps.stepsOutside
import app.ritm.core.time.HOUR
import app.ritm.core.time.Interval
import java.time.LocalDate
import java.time.Period
import kotlin.math.roundToInt

enum class Sex { MALE, FEMALE }

data class Body(val sex: Sex, val heightCm: Double, val birthDate: LocalDate)

enum class GoalType { LOSE, KEEP, GAIN }

data class WeightGoal(val type: GoalType, val paceKgPerWeek: Double)

object Energy {
    const val KCAL_PER_KG = 7_700.0
    /** Чистый расход на ходьбу сверх покоя, ккал на кг на км. */
    const val WALK_NET_KCAL_PER_KG_KM = 0.5
    /** Силовая тренировка, MET. */
    const val STRENGTH_MET = 3.5

    fun ageYears(birth: LocalDate, on: LocalDate): Int = Period.between(birth, on).years

    /** Базовый расход, Миффлин — Сан Жеор, ккал/сутки. */
    fun bmrPerDay(body: Body, weightKg: Double, on: LocalDate): Double {
        val base = 10 * weightKg + 6.25 * body.heightCm - 5 * ageYears(body.birthDate, on)
        return base + if (body.sex == Sex.MALE) 5 else -161
    }

    fun strideMeters(body: Body): Double = body.heightCm / 100 * if (body.sex == Sex.MALE) 0.415 else 0.413

    fun stepsKcal(steps: Int, weightKg: Double, body: Body): Double =
        WALK_NET_KCAL_PER_KG_KM * weightKg * steps * strideMeters(body) / 1000

    /** Чистый расход тренировки сверх покоя (покой уже в базовом). */
    fun workoutKcal(minutes: Double, weightKg: Double, met: Double = STRENGTH_MET): Double =
        (met - 1) * weightKg * minutes / 60

    /** Целевой дефицит в сутки (отрицательный — профицит для набора). */
    fun dailyDeficit(goal: WeightGoal): Double = when (goal.type) {
        GoalType.LOSE -> goal.paceKgPerWeek * KCAL_PER_KG / 7
        GoalType.GAIN -> -goal.paceKgPerWeek * KCAL_PER_KG / 7
        GoalType.KEEP -> 0.0
    }

    fun minimumIntake(body: Body): Double = if (body.sex == Sex.MALE) 1_500.0 else 1_200.0
}

data class CycleEnergy(val bmr: Double, val steps: Double, val workouts: Double, val calibration: Double) {
    val activity: Double get() = steps + workouts
    val total: Double get() = (bmr + activity) * calibration
}

/** Реальный расход за цикл: базовый за час × длина + шаги (без тренировок) + тренировки. */
fun cycleExpenditure(
    body: Body,
    weightKg: Double,
    cycle: Interval,
    date: LocalDate,
    steps: List<StepInterval>,
    workouts: List<Interval>,
    calibration: Double = 1.0,
): CycleEnergy {
    val bmr = Energy.bmrPerDay(body, weightKg, date) / 24 * (cycle.duration.toDouble() / HOUR)
    val walkSteps = stepsOutside(cycle, steps, workouts)
    val workoutMinutes = workouts.sumOf { it.overlap(cycle) } / 60_000.0
    return CycleEnergy(
        bmr = bmr,
        steps = Energy.stepsKcal(walkSteps, weightKg, body),
        workouts = Energy.workoutKcal(workoutMinutes, weightKg),
        calibration = calibration,
    )
}

/** Средняя активность (шаги + тренировки) по последним полным циклам; нужно минимум 3. */
fun averageActivity(cycles: List<CycleEnergy>, window: Int = 14): Double? =
    cycles.takeLast(window).takeIf { it.size >= 3 }?.map { it.activity }?.average()

/**
 * План на день (стабилен весь день): базовый + обычная активность − целевой дефицит.
 * Пока истории нет, обычная активность = 20% базового (низкая активность).
 */
fun dailyPlan(
    body: Body,
    weightKg: Double,
    date: LocalDate,
    goal: WeightGoal,
    averageActivity: Double?,
    calibration: Double = 1.0,
): Int {
    val bmr = Energy.bmrPerDay(body, weightKg, date)
    val tdee = (bmr + (averageActivity ?: 0.2 * bmr)) * calibration
    val target = tdee - Energy.dailyDeficit(goal)
    return maxOf(target, Energy.minimumIntake(body)).roundToInt()
}
