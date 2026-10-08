package app.ritm.data

import android.content.Context
import app.ritm.core.food.FoodHit
import app.ritm.core.food.suggestCombos
import app.ritm.core.food.suggestFood
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.put
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import kotlin.math.roundToInt

@Serializable
data class ComboItem(val productId: Long, val grams: Double)

@Serializable
private data class SeedProduct(val n: String, val k: Double, val p: Double, val f: Double, val c: Double, val g: Double)

/** Выбор в списке еды: комплект или продукт. */
sealed interface FoodChoice {
    val title: String
    data class Product(val row: ProductRow) : FoodChoice { override val title get() = row.name }
    data class Combo(val row: ComboRow, val kcal: Int) : FoodChoice { override val title get() = row.name }
}

val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

/**
 * Единственная точка записи данных. Каждая запись сразу кладётся в журнал событий для сервера.
 */
class Repo(private val context: Context, val db: RitmDb, val settings: Settings) {
    val events = Events(db)
    private val portions = HashMap<String, Double>()

    /** Время пробуждения текущего дня (обновляет модель дня). */
    @Volatile var currentWake: Long? = null

    // ——— Продукты ———

    suspend fun seedProducts() = withContext(Dispatchers.IO) {
        val seed = context.assets.open("products.json").bufferedReader().use { json.decodeFromString<List<SeedProduct>>(it.readText()) }
        seed.forEach { portions[it.n] = it.g }
        if (db.food().productCount() == 0) {
            db.food().insertProducts(seed.map { ProductRow(name = it.n, kcal100 = it.k, protein = it.p, fat = it.f, carbs = it.c) })
        }
    }

    /** Граммы по умолчанию: прошлый раз, иначе обычная порция, иначе 100. */
    fun defaultGrams(p: ProductRow): Double = p.lastGrams ?: portions[p.name] ?: 100.0

    suspend fun foodSuggestions(now: Long, wakeAt: Long?): List<FoodChoice> = withContext(Dispatchers.IO) {
        val from = now - 30L * 24 * 3600_000
        val hist = db.food().food(from, now)
        val wake = wakeAt ?: now
        val hits = hist.map {
            val key = if (it.comboId != null) "c:${it.comboId}" else "p:${it.productId}"
            FoodHit(key, it.at, it.sinceWake ?: ((it.at - wakeOfEntry(it.at, wake)) / 60_000).toInt())
        }.distinctBy { it.itemKey to it.at / 60_000 }
        val keys = suggestFood(hits, now, ((now - wake) / 60_000).toInt(), limit = 14)
        val combos = db.food().combos().associateBy { "c:${it.id}" }
        val productIds = keys.filter { it.startsWith("p:") }.mapNotNull { it.removePrefix("p:").toLongOrNull() }
        val products = db.food().products(productIds).associateBy { "p:${it.id}" }
        val ordered = keys.mapNotNull { k -> combos[k]?.let { FoodChoice.Combo(it, comboKcal(it)) } ?: products[k]?.let { FoodChoice.Product(it) } }
        val rest = (db.food().combos().map { FoodChoice.Combo(it, comboKcal(it)) } + db.food().recent().map { FoodChoice.Product(it) })
            .filter { c -> ordered.none { it.title == c.title } }
        (ordered + rest).take(20)
    }

    // Приблизительно: для старых записей время пробуждения неизвестно — берём то же смещение суток.
    private fun wakeOfEntry(at: Long, currentWake: Long): Long {
        val day = 24 * 3600_000L
        val offsetInDay = Math.floorMod(currentWake, day)
        val base = at - Math.floorMod(at - offsetInDay, day)
        return base
    }

    suspend fun searchProducts(q: String): List<ProductRow> = withContext(Dispatchers.IO) { db.food().search(q.trim()) }

    suspend fun comboKcal(c: ComboRow): Int {
        val items = json.decodeFromString<List<ComboItem>>(c.items)
        val ps = db.food().products(items.map { it.productId }).associateBy { it.id }
        return items.sumOf { (ps[it.productId]?.kcal100 ?: 0.0) * it.grams / 100 }.roundToInt()
    }

    suspend fun addFood(p: ProductRow, grams: Double, at: Long = System.currentTimeMillis(), comboId: Long? = null): FoodRow = withContext(Dispatchers.IO) {
        val kcal = p.kcal100 * grams / 100
        val sinceWake = currentWake?.let { ((at - it) / 60_000).toInt() }?.takeIf { it >= 0 }
        val row = FoodRow(at = at, productId = p.id, name = p.name, grams = grams, kcal = kcal, comboId = comboId, uid = newUid(), sinceWake = sinceWake)
        val id = db.food().insertFood(row)
        db.food().updateProduct(p.copy(lastGrams = grams, usedAt = at, uses = p.uses + 1))
        events.emit("food.add", at, row.uid) {
            put("name", p.name); put("grams", grams); put("kcal", kcal)
            p.protein?.let { put("protein", it * grams / 100) }; p.fat?.let { put("fat", it * grams / 100) }; p.carbs?.let { put("carbs", it * grams / 100) }
            comboId?.let { put("comboId", it) }
        }
        row.copy(id = id)
    }

    suspend fun addCombo(c: ComboRow, at: Long = System.currentTimeMillis()): List<FoodRow> = withContext(Dispatchers.IO) {
        val items = json.decodeFromString<List<ComboItem>>(c.items)
        val ps = db.food().products(items.map { it.productId }).associateBy { it.id }
        db.food().updateCombo(c.copy(usedAt = at))
        items.mapNotNull { i -> ps[i.productId]?.let { addFood(it, i.grams, at, c.id) } }
    }

    suspend fun saveCombo(name: String, items: List<ComboItem>) = withContext(Dispatchers.IO) {
        db.food().insertCombo(ComboRow(name = name, items = json.encodeToString(items)))
    }

    /** Предложение комплекта: одинаковый набор 3+ раз за 30 дней. */
    suspend fun comboSuggestion(now: Long): List<ProductRow>? = withContext(Dispatchers.IO) {
        val hist = db.food().food(now - 30L * 24 * 3600_000, now).filter { it.comboId == null && it.productId != null }
        val existing = db.food().combos().map { c -> json.decodeFromString<List<ComboItem>>(c.items).map { "p:${it.productId}" }.toSet() }.toSet()
        val hits = hist.map { FoodHit("p:${it.productId}", it.at, 0) }
        val dismissed = db.days().flag("combo_dismissed|all")?.split(';')?.toSet() ?: emptySet()
        val s = suggestCombos(hits, now, existing).firstOrNull { it.sorted().joinToString(",") !in dismissed } ?: return@withContext null
        db.food().products(s.map { it.removePrefix("p:").toLong() })
    }

    suspend fun dismissComboSuggestion(ids: List<Long>) = withContext(Dispatchers.IO) {
        val key = "combo_dismissed|all"
        val cur = db.days().flag(key)?.split(';')?.toMutableSet() ?: mutableSetOf()
        cur += ids.map { "p:$it" }.sorted().joinToString(",")
        db.days().setFlag(DayFlagRow(key, cur.joinToString(";")))
    }

    /** Последние граммы этих продуктов сегодня — для комплекта. */
    suspend fun lastGramsToday(ids: List<Long>, since: Long): List<ComboItem> = withContext(Dispatchers.IO) {
        val today = db.food().food(since, System.currentTimeMillis())
        ids.map { id -> ComboItem(id, today.lastOrNull { it.productId == id }?.grams ?: db.food().product(id)?.let(::defaultGrams) ?: 100.0) }
    }

    suspend fun updateFoodGrams(f: FoodRow, grams: Double) = withContext(Dispatchers.IO) {
        val kcal = if (f.grams > 0) f.kcal / f.grams * grams else f.kcal
        db.food().updateFood(f.copy(grams = grams, kcal = kcal))
        events.emit("food.update", System.currentTimeMillis()) { put("uid", f.uid); put("grams", grams); put("kcal", kcal) }
    }

    suspend fun deleteFood(f: FoodRow) = withContext(Dispatchers.IO) {
        db.food().deleteFood(f)
        events.emit("food.delete") { put("uid", f.uid) }
    }

    suspend fun restoreFood(f: FoodRow) = withContext(Dispatchers.IO) {
        db.food().insertFood(f.copy(id = 0))
        events.emit("food.add", f.at, newUid()) { put("name", f.name); put("grams", f.grams); put("kcal", f.kcal); put("restoredUid", f.uid) }
    }

    suspend fun addOwnProduct(name: String, kcal100: Double, protein: Double?, fat: Double?, carbs: Double?): ProductRow = withContext(Dispatchers.IO) {
        val row = ProductRow(name = name.trim(), kcal100 = kcal100, protein = protein, fat = fat, carbs = carbs, own = true)
        val id = db.food().insertProduct(row)
        events.emit("product.add") { put("name", row.name); put("kcal100", kcal100) }
        row.copy(id = id)
    }

    fun foodSince(from: Long): Flow<List<FoodRow>> = db.food().foodFlow(from)

    // ——— Вес ———

    suspend fun addWeight(kg: Double, at: Long = System.currentTimeMillis()): WeightRow = withContext(Dispatchers.IO) {
        val row = WeightRow(at = at, kg = kg, uid = newUid())
        val id = db.food().insertWeight(row)
        events.emit("weight.add", at, row.uid) { put("kg", kg) }
        row.copy(id = id)
    }

    suspend fun deleteWeight(w: WeightRow) = withContext(Dispatchers.IO) {
        db.food().deleteWeight(w)
        events.emit("weight.delete") { put("uid", w.uid) }
    }

    suspend fun lastWeight(): WeightRow? = withContext(Dispatchers.IO) { db.food().lastWeight() }

    // ——— Тренировки ———

    fun activeWorkout() = db.workouts().activeFlow()
    fun setsOf(workoutId: Long) = db.workouts().setsFlow(workoutId)
    fun exercises() = db.workouts().exercisesFlow()

    suspend fun startWorkout(auto: Boolean, placeId: Long? = null): WorkoutRow = withContext(Dispatchers.IO) {
        db.workouts().active()?.let { return@withContext it }
        val now = System.currentTimeMillis()
        val row = WorkoutRow(start = now, auto = auto, placeId = placeId, uid = newUid())
        val id = db.workouts().insertWorkout(row)
        events.emit("workout.start", now, row.uid) { put("auto", auto); placeId?.let { put("placeId", it) } }
        row.copy(id = id)
    }

    /** Завершить. Тренировка без подходов удаляется. */
    suspend fun endWorkout(at: Long = System.currentTimeMillis()) = withContext(Dispatchers.IO) {
        val w = db.workouts().active() ?: return@withContext
        val sets = db.workouts().sets(w.id)
        if (sets.isEmpty()) {
            db.workouts().deleteWorkout(w)
            events.emit("workout.discard") { put("uid", w.uid) }
        } else {
            val end = maxOf(sets.last().at, minOf(at, sets.last().at + 30 * 60_000L))
            db.workouts().updateWorkout(w.copy(end = end))
            events.emit("workout.end", end) { put("uid", w.uid); put("sets", sets.size) }
        }
    }

    suspend fun chooseExercise(name: String): ExerciseRow = withContext(Dispatchers.IO) {
        val clean = name.trim().replaceFirstChar { it.uppercase() }
        val ex = db.workouts().exercise(clean) ?: run {
            db.workouts().insertExercise(ExerciseRow(name = clean))
            db.workouts().exercise(clean)!!
        }
        val w = db.workouts().active() ?: startWorkout(auto = false)
        db.workouts().updateWorkout(w.copy(currentExerciseId = ex.id))
        ex
    }

    suspend fun exercise(id: Long): ExerciseRow? = withContext(Dispatchers.IO) { db.workouts().exerciseById(id) }

    /** Мой прошлый ввод в этом упражнении (для полей). */
    suspend fun lastSet(exerciseId: Long): SetRow? = withContext(Dispatchers.IO) { db.workouts().lastSet(exerciseId) }

    suspend fun addSet(kg: Double, reps: Int): SetRow? = withContext(Dispatchers.IO) {
        val w = db.workouts().active() ?: return@withContext null
        val exId = w.currentExerciseId ?: return@withContext null
        val now = System.currentTimeMillis()
        val row = SetRow(workoutId = w.id, exerciseId = exId, at = now, kg = kg, reps = reps, uid = newUid())
        val id = db.workouts().insertSet(row)
        db.workouts().exerciseById(exId)?.let { db.workouts().updateExercise(it.copy(uses = it.uses + 1, usedAt = now)) }
        val ex = db.workouts().exerciseById(exId)?.name ?: ""
        events.emit("set.add", now, row.uid) { put("workout", w.uid); put("exercise", ex); put("kg", kg); put("reps", reps) }
        row.copy(id = id)
    }

    /** Подход кнопкой: повторить мой последний ввод в текущем упражнении. */
    suspend fun addSetLikeLast(): SetRow? = withContext(Dispatchers.IO) {
        val w = db.workouts().active() ?: return@withContext null
        val exId = w.currentExerciseId ?: return@withContext null
        val draft = WorkoutDraft.get(exId) ?: db.workouts().lastSet(exId)?.let { it.kg to it.reps } ?: return@withContext null
        addSet(draft.first, draft.second)
    }

    suspend fun updateSet(s: SetRow, kg: Double, reps: Int) = withContext(Dispatchers.IO) {
        db.workouts().updateSet(s.copy(kg = kg, reps = reps))
        events.emit("set.update") { put("uid", s.uid); put("kg", kg); put("reps", reps) }
    }

    suspend fun deleteSet(s: SetRow) = withContext(Dispatchers.IO) {
        db.workouts().deleteSet(s)
        events.emit("set.delete") { put("uid", s.uid) }
    }

    // ——— Задачи ———

    fun openTasks() = db.tasks().openFlow()

    suspend fun addTask(title: String, date: LocalDate?, time: String?): TaskRow = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        val row = TaskRow(title = title.trim(), date = date?.toString(), time = time, createdAt = now, uid = newUid())
        val id = db.tasks().insert(row)
        events.emit("task.add", now, row.uid) { put("title", row.title); date?.let { put("date", it.toString()) }; time?.let { put("time", it) } }
        row.copy(id = id)
    }

    suspend fun updateTask(t: TaskRow) = withContext(Dispatchers.IO) {
        db.tasks().update(t)
        events.emit("task.update") { put("uid", t.uid); put("title", t.title); put("date", t.date ?: ""); put("time", t.time ?: "") }
    }

    suspend fun completeTask(t: TaskRow) = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        db.tasks().update(t.copy(done = true, doneAt = now))
        events.emit("task.done", now) { put("uid", t.uid); put("carry", t.carry) }
    }

    suspend fun uncompleteTask(t: TaskRow) = withContext(Dispatchers.IO) {
        db.tasks().update(t.copy(done = false, doneAt = null))
        events.emit("task.undone") { put("uid", t.uid) }
    }

    suspend fun deleteTask(t: TaskRow) = withContext(Dispatchers.IO) {
        db.tasks().delete(t)
        events.emit("task.delete") { put("uid", t.uid) }
    }

    suspend fun restoreTask(t: TaskRow) = withContext(Dispatchers.IO) { db.tasks().insert(t.copy(id = 0)) }

    suspend fun task(id: Long) = withContext(Dispatchers.IO) { db.tasks().byId(id) }

    // ——— Заметки ———

    fun notes() = db.notes().allFlow()

    suspend fun addNote(text: String): NoteRow = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        val row = NoteRow(at = now, text = text, uid = newUid())
        val id = db.notes().insert(row)
        row.copy(id = id)
    }

    /** Текст заметки сохраняется на ходу; событие уходит один раз при закрытии. */
    suspend fun saveNoteText(n: NoteRow, text: String) = withContext(Dispatchers.IO) { db.notes().update(n.copy(text = text)) }

    suspend fun publishNote(id: Long) = withContext(Dispatchers.IO) {
        val n = db.notes().byId(id) ?: return@withContext
        if (n.text.isBlank() && n.audioPath == null && n.kind != "moment") { db.notes().delete(n); return@withContext }
        events.emit("note.save", n.at, newUid()) { put("uid", n.uid); put("kind", n.kind); put("text", n.text) }
    }

    suspend fun addVoiceNote(file: File, durationMs: Long, silent: Boolean = false): NoteRow = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        val text = if (silent) "Запись без звука: микрофон не дали в фоне. Откройте Ритм один раз — голос заработает" else ""
        val row = NoteRow(at = now, text = text, audioPath = file.absolutePath, audioMs = durationMs, kind = "voice", uid = newUid())
        val id = db.notes().insert(row)
        val audio = android.util.Base64.encodeToString(file.readBytes(), android.util.Base64.NO_WRAP)
        events.emit("note.voice", now, row.uid) { put("durationMs", durationMs); put("silent", silent); put("audioM4aBase64", audio) }
        row.copy(id = id)
    }

    suspend fun addMoment(): NoteRow = withContext(Dispatchers.IO) {
        val now = System.currentTimeMillis()
        val row = NoteRow(at = now, text = "", kind = "moment", uid = newUid())
        val id = db.notes().insert(row)
        events.emit("moment", now, row.uid)
        row.copy(id = id)
    }

    suspend fun deleteNote(n: NoteRow) = withContext(Dispatchers.IO) {
        db.notes().delete(n)
        events.emit("note.delete") { put("uid", n.uid) }
    }

    suspend fun restoreNote(n: NoteRow) = withContext(Dispatchers.IO) { db.notes().insert(n.copy(id = 0)) }

    suspend fun note(id: Long) = withContext(Dispatchers.IO) { db.notes().byId(id) }

    // ——— Флаги дня ———

    suspend fun flag(date: LocalDate, name: String): Boolean = withContext(Dispatchers.IO) { db.days().flag("$date|$name") != null }

    suspend fun setFlag(date: LocalDate, name: String) = withContext(Dispatchers.IO) {
        db.days().setFlag(DayFlagRow("$date|$name"))
        events.emit("day.flag") { put("date", date.toString()); put("flag", name) }
    }

    suspend fun markSleep(start: Long, end: Long, kind: String) = withContext(Dispatchers.IO) {
        db.days().insertMark(SleepMarkRow(start = start, end = end, kind = kind))
        events.emit("sleep.mark") { put("start", start); put("end", end); put("kind", kind) }
    }

    fun localDate(at: Long): LocalDate = Instant.ofEpochMilli(at).atZone(ZoneId.systemDefault()).toLocalDate()
}

/** Черновик полей тренировки: мой последний ввод в упражнении (до записи подхода). */
object WorkoutDraft {
    private val drafts = HashMap<Long, Pair<Double, Int>>()
    fun set(exerciseId: Long, kg: Double, reps: Int) { drafts[exerciseId] = kg to reps }
    fun get(exerciseId: Long): Pair<Double, Int>? = drafts[exerciseId]
}
