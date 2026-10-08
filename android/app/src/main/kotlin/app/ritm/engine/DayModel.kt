package app.ritm.engine

import android.content.Context
import app.ritm.core.day.DayEngine
import app.ritm.core.day.NowItem
import app.ritm.core.day.nowLine
import app.ritm.core.energy.CycleEnergy
import app.ritm.core.energy.averageActivity
import app.ritm.core.energy.cycleExpenditure
import app.ritm.core.energy.dailyPlan
import app.ritm.core.energy.weightTrend
import app.ritm.core.food.shouldRemindFood
import app.ritm.core.food.weightCardVisible
import app.ritm.core.sleep.Confidence
import app.ritm.core.sleep.SleepDetector
import app.ritm.core.sleep.SleepEstimate
import app.ritm.core.sleep.SleepSignals
import app.ritm.core.steps.StepInterval
import app.ritm.core.tasks.TaskItem
import app.ritm.core.tasks.carryOver
import app.ritm.core.time.DAY
import app.ritm.core.time.HOUR
import app.ritm.core.time.Interval
import app.ritm.data.DayFlagRow
import app.ritm.data.Repo
import app.ritm.data.SpanRow
import app.ritm.data.json
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.put
import java.time.LocalDate
import java.time.ZoneId
import kotlin.math.roundToInt

/** Итог закрытого дня, хранится локально для калибровки. */
@Serializable
data class DaySummary(val date: String, val intake: Double, val estimated: Double, val complete: Boolean)

@Serializable
data class PendingPlace(val lat: Double, val lon: Double, val start: Long, val guess: String? = null)

/** Всё, что нужно экрану «Сегодня» и шторке. */
data class DayState(
    val ready: Boolean = false,
    val now: Long = 0,
    val wakeAt: Long? = null,
    val date: LocalDate? = null,
    val sleeping: Boolean = false,
    val plan: Int? = null,
    val eaten: Int = 0,
    val nowItem: NowItem? = null,
    val problem: Access? = null,
    val pendingPlace: PendingPlace? = null,
    val unsureSleep: SleepEstimate? = null,
    val workoutActive: Boolean = false,
    val weightCard: Boolean = false,
    val lastWeightKg: Double? = null,
) {
    val remaining: Int? get() = plan?.let { it - eaten }
}

/**
 * Собирает ядро логики и данные в одно состояние. Пересчитывается после записей,
 * при открытии приложения и по тику фоновой службы.
 */
class DayModel(private val context: Context, private val repo: Repo) {
    private val _state = MutableStateFlow(DayState())
    val state: StateFlow<DayState> = _state
    private val mutex = Mutex()
    private val detector = SleepDetector()
    private val engine = DayEngine()

    suspend fun refresh(): DayState = mutex.withLock {
        withContext(Dispatchers.IO) { compute() }.also { _state.value = it }
    }

    private suspend fun compute(): DayState {
        val now = System.currentTimeMillis()
        val zone = ZoneId.systemDefault()
        val prefs = repo.settings.get()
        val db = repo.db
        endStaleWorkout(now)
        val from = now - 16 * DAY
        val origin = maxOf(prefs.installedAt.takeIf { it > 0 } ?: from, from)

        val sleeps = sleepEstimates(from, now, zone)
        val marks = db.days().marks(from)
        val rejected = marks.filter { it.kind == "rejected" }.map { Interval(it.start, it.end) }
        val confirmed = marks.filter { it.kind == "confirmed" || it.kind == "edited" }.map { Interval(it.start, it.end) }
        val detected = sleeps.filter { s -> rejected.none { it.overlap(s.interval) > s.interval.duration / 2 } }
        val main = (detected.filter { it.night }.map { it.interval }
            .filter { iv -> confirmed.none { it.overlap(iv) > 0 } } + confirmed).sortedBy { it.start }

        val timeline = engine.build(main, origin, now) { zone }
        val current = timeline.current
        val wakeAt = current?.start
        repo.currentWake = wakeAt
        val date = current?.date

        // Карточка «сон под вопросом»: сон перед этим днём с низкой уверенностью и без решения человека.
        val prevSleep = timeline.days.dropLast(if (current != null) 1 else 0).lastOrNull()?.sleepAfter
        val unsure = prevSleep?.let { ps ->
            detected.firstOrNull { it.interval.overlap(ps) > 0 && it.confidence == Confidence.LOW }
                ?.takeIf { s -> marks.none { it.overlap(s.interval) } }
        }

        val eaten = if (wakeAt != null) db.food().food(wakeAt, now + 1).sumOf { it.kcal }.roundToInt() else 0
        val weights = db.food().weights(now - 60 * DAY)
        val trend = weightTrend(weights.map { repo.localDate(it.at) to it.kg })
        val weightNow = trend.values.lastOrNull() ?: weights.lastOrNull()?.kg

        val body = prefs.body
        val plan = if (body != null && weightNow != null && date != null) {
            val steps = db.signals().steps(from).map { StepInterval(Interval(it.start, it.end), it.count) }
            val workouts = db.workouts().workouts(from).map { Interval(it.start, it.end ?: now) }
            val cycles: List<CycleEnergy> = timeline.days.indices.filter { !timeline.days[it].ongoing && timeline.days.getOrNull(it + 1) != null }.map { i ->
                val cycle = timeline.cycleOf(i, now)
                cycleExpenditure(body, weightNow, cycle, timeline.days[i].date, steps, workouts)
            }
            dailyPlan(body, weightNow, date, prefs.goal, averageActivity(cycles), prefs.calibration)
        } else null

        val workout = db.workouts().active()
        val weighed = wakeAt != null && weights.any { it.at >= wakeAt }
        val weightDismissed = date != null && db.days().flag("$date|weight_dismissed") != null
        val weightCard = weightCardVisible(now, wakeAt, weighed, weightDismissed)
        val pending = prefs.pendingPlace.takeIf { it.isNotBlank() }?.let { runCatching { json.decodeFromString<PendingPlace>(it) }.getOrNull() }
        val problem = if (prefs.onboarded) Permissions.missing(context).firstOrNull { it != Access.LOCATION_ALWAYS || Permissions.granted(context, Access.LOCATION) } else null

        if (date != null) dailyHousekeeping(date, wakeAt!!, now)

        return DayState(
            ready = true,
            now = now,
            wakeAt = wakeAt,
            date = date,
            sleeping = timeline.sleepingSince != null,
            plan = plan,
            eaten = eaten,
            nowItem = nowLine(workout != null, problem != null, weightCard, pending != null, unsure != null),
            problem = problem,
            pendingPlace = pending,
            unsureSleep = unsure,
            workoutActive = workout != null,
            weightCard = weightCard,
            lastWeightKg = weights.lastOrNull()?.kg,
        )
    }

    /**
     * Тренировка вне зала завершается сама после 30 минут без подходов.
     * Автоматическая (в зале) завершается при выходе; страховка — 4 часа.
     */
    private suspend fun endStaleWorkout(now: Long) {
        val w = repo.db.workouts().active() ?: return
        val last = repo.db.workouts().sets(w.id).lastOrNull()?.at ?: w.start
        val limit = if (w.auto) 4 * HOUR else 30 * 60_000L
        if (now - last > limit) {
            repo.endWorkout(now)
            WorkoutClock.clear(context)
        }
    }

    private suspend fun sleepEstimates(from: Long, now: Long, zone: ZoneId): List<SleepEstimate> {
        val s = repo.db.signals()
        fun spans(rows: List<SpanRow>) = rows.map { Interval(maxOf(it.start, from), maxOf(it.end ?: now, it.start)) }
        val homeIds = repo.db.places().places().filter { it.name.trim().equals("Дом", ignoreCase = true) }.map { it.id }
        val home = homeIds.flatMap { spans(s.spans("place:$it", from)) }
        val signals = SleepSignals(
            activity = s.pings(from),
            charging = spans(s.spans("charging", from)),
            dark = spans(s.spans("dark", from)),
            atHome = home,
            still = spans(s.spans("still", from)),
            steps = s.steps(from).map { StepInterval(Interval(it.start, it.end), it.count) },
            pcCovered = spans(s.spans("pc", from)),
        )
        return detector.detect(signals, now, zone)
    }

    /** Раз в день: перенос задач. По ходу дня: одно напоминание о еде. */
    private suspend fun dailyHousekeeping(date: LocalDate, wakeAt: Long, now: Long) {
        val db = repo.db
        val carryKey = "$date|carry"
        if (db.days().flag(carryKey) == null) {
            val open = db.tasks().open()
            val moved = carryOver(open.map { TaskItem(it.id, it.date?.let(LocalDate::parse), it.done, it.carry) }, date)
            moved.zip(open).filter { (m, o) -> m.carryCount != o.carry }.forEach { (m, o) ->
                db.tasks().update(o.copy(date = m.date?.toString(), carry = m.carryCount))
                repo.events.emit("task.carry") { put("uid", o.uid); put("carry", m.carryCount) }
            }
            db.days().setFlag(DayFlagRow(carryKey))
            repo.events.emit("day.start", wakeAt) { put("date", date.toString()) }
        }

        val loggedToday = db.food().food(wakeAt, now + 1).isNotEmpty()
        val skip = db.days().flag("$date|skip_food") != null
        val reminded = db.days().flag("$date|food_reminded") != null
        if (!loggedToday && !skip && !reminded) {
            val offsets = firstMealOffsets(now)
            if (shouldRemindFood(now, wakeAt, offsets, loggedToday, skip, reminded)) {
                db.days().setFlag(DayFlagRow("$date|food_reminded"))
                Notifications.foodReminder(context)
            }
        }
    }

    /** Через сколько минут после пробуждения обычно первая еда (по последним 14 дням). */
    private suspend fun firstMealOffsets(now: Long): List<Int> {
        val food = repo.db.food().food(now - 14 * DAY, now)
        return food.filter { it.sinceWake != null }
            .groupBy { repo.localDate(it.at - it.sinceWake!! * 60_000L) }
            .mapNotNull { (_, rows) -> rows.minOfOrNull { it.sinceWake!! } }
            .filter { it < 16 * 60 }
    }

    /** Неделя данных о дне для ИИ: итог закрытого цикла уходит событием один раз. */
    suspend fun summarizeClosedDays() = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        val prefs = repo.settings.get()
        val body = prefs.body ?: return@withContext
        val zone = ZoneId.systemDefault()
        val from = now - 16 * DAY
        val origin = maxOf(prefs.installedAt.takeIf { it > 0 } ?: from, from)
        val db = repo.db
        val sleeps = sleepEstimates(from, now, zone)
        val marks = db.days().marks(from)
        val rejected = marks.filter { it.kind == "rejected" }.map { Interval(it.start, it.end) }
        val confirmed = marks.filter { it.kind == "confirmed" || it.kind == "edited" }.map { Interval(it.start, it.end) }
        val main = (sleeps.filter { it.night && rejected.none { r -> r.overlap(it.interval) > 0 } }.map { it.interval }
            .filter { iv -> confirmed.none { it.overlap(iv) > 0 } } + confirmed).sortedBy { it.start }
        val tl = engine.build(main, origin, now) { zone }
        val steps = db.signals().steps(from).map { StepInterval(Interval(it.start, it.end), it.count) }
        val workouts = db.workouts().workouts(from).filter { it.end != null }.map { Interval(it.start, it.end!!) }
        val weight = db.food().lastWeight()?.kg ?: return@withContext
        for (i in tl.days.indices) {
            val d = tl.days[i]
            val next = tl.days.getOrNull(i + 1) ?: continue
            val key = "summary|${d.start}"
            if (db.days().flag(key) != null) continue
            val cycle = Interval(d.start, next.start)
            val e = cycleExpenditure(body, weight, cycle, d.date, steps, workouts, prefs.calibration)
            val raw = cycleExpenditure(body, weight, cycle, d.date, steps, workouts, 1.0).total
            val food = db.food().food(cycle.start, cycle.end)
            val skip = db.days().flag("${d.date}|skip_food") != null
            val sleep = d.sleepAfter
            val est = sleep?.let { s -> sleeps.firstOrNull { it.interval.overlap(s) > 0 } }
            repo.events.emit("day.summary", d.start) {
                put("date", d.date.toString()); put("wake", d.start); put("sleepStart", d.end ?: 0); put("nextWake", next.start)
                put("noSleep", d.noSleep); put("intake", food.sumOf { it.kcal }); put("expenditure", e.total)
                put("bmr", e.bmr); put("stepsKcal", e.steps); put("workoutKcal", e.workouts)
                put("steps", app.ritm.core.steps.stepsIn(cycle, steps)); put("foodComplete", food.isNotEmpty() || skip)
                put("calibration", prefs.calibration); put("calibrated", prefs.calibrated)
                est?.let { put("sleepConfidence", it.confidence.name); put("sleepInterruptions", it.interruptions) }
            }
            val complete = food.isNotEmpty() || skip
            db.days().setFlag(DayFlagRow(key, json.encodeToString(DaySummary.serializer(), DaySummary(d.date.toString(), food.sumOf { it.kcal }, raw, complete))))
        }
    }

    companion object {
        const val WEIGHT_CARD_HOURS = 4 * HOUR
    }
}

private fun app.ritm.data.SleepMarkRow.overlap(iv: Interval): Boolean = start < iv.end && end > iv.start
