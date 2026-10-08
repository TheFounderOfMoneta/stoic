package app.ritm.data

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/** Журнал событий для сервера: UUID, время UTC + смещение пояса, JSON. */
@Entity(tableName = "outbox", indices = [Index("sent")])
data class OutboxEvent(
    @PrimaryKey val id: String,
    val type: String,
    val at: Long,
    val offsetSec: Int,
    val payload: String,
    val sent: Boolean = false,
)

@Entity(tableName = "steps", indices = [Index("end")])
data class StepRow(@PrimaryKey(autoGenerate = true) val id: Long = 0, val start: Long, val end: Long, val count: Int)

/** Момент активности человека: касание, разблокировка, ввод на ПК. */
@Entity(tableName = "pings", indices = [Index("at")])
data class PingRow(@PrimaryKey(autoGenerate = true) val id: Long = 0, val at: Long, val source: String)

/** Интервал состояния: зарядка, темно, неподвижен, транспорт, экран включён. end = null — ещё идёт. */
@Entity(tableName = "spans", indices = [Index("kind"), Index("start")])
data class SpanRow(@PrimaryKey(autoGenerate = true) val id: Long = 0, val kind: String, val start: Long, val end: Long?)

@Entity(tableName = "fixes", indices = [Index("time")])
data class FixRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val time: Long,
    val lat: Double,
    val lon: Double,
    val acc: Double,
    val provider: String,
    /** accepted / rejected:<причина> / quarantined / promoted / dropped */
    val verdict: String,
    val forPlaces: Boolean,
    val placeId: Long? = null,
)

@Entity(tableName = "places")
data class PlaceRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val name: String,
    val lat: Double,
    val lon: Double,
    val radius: Double = 200.0,
    val createdAt: Long,
    /** Здесь тренируюсь: тренировка начинается сама через 5 минут. */
    @ColumnInfo(defaultValue = "0") val isGym: Boolean = false,
)

@Entity(tableName = "stays", indices = [Index("start")])
data class StayRow(@PrimaryKey(autoGenerate = true) val id: Long = 0, val start: Long, val end: Long, val lat: Double, val lon: Double)

@Entity(tableName = "dismissed_spots")
data class DismissedSpot(@PrimaryKey(autoGenerate = true) val id: Long = 0, val lat: Double, val lon: Double)

@Entity(tableName = "products", indices = [Index("name")])
data class ProductRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val name: String,
    val kcal100: Double,
    val protein: Double? = null,
    val fat: Double? = null,
    val carbs: Double? = null,
    /** Добавлен или исправлен мной — всплывает первым. */
    val own: Boolean = false,
    val lastGrams: Double? = null,
    val usedAt: Long? = null,
    val uses: Int = 0,
    /** Штрихкод (EAN), если продукт найден сканером. */
    val barcode: String? = null,
)

@Entity(tableName = "combos")
data class ComboRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val name: String,
    /** JSON: [{"productId":1,"grams":60.0}] */
    val items: String,
    val usedAt: Long? = null,
)

@Entity(tableName = "food", indices = [Index("at")])
data class FoodRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val at: Long,
    val productId: Long?,
    val name: String,
    val grams: Double,
    val kcal: Double,
    val comboId: Long? = null,
    val uid: String,
    /** Минут от пробуждения в момент еды — для «обычно сейчас». */
    val sinceWake: Int? = null,
)

@Entity(tableName = "weights", indices = [Index("at")])
data class WeightRow(@PrimaryKey(autoGenerate = true) val id: Long = 0, val at: Long, val kg: Double, val uid: String)

@Entity(tableName = "exercises", indices = [Index(value = ["name"], unique = true)])
data class ExerciseRow(@PrimaryKey(autoGenerate = true) val id: Long = 0, val name: String, val uses: Int = 0, val usedAt: Long? = null)

@Entity(tableName = "workouts", indices = [Index("start")])
data class WorkoutRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val start: Long,
    val end: Long? = null,
    val placeId: Long? = null,
    val auto: Boolean = false,
    val currentExerciseId: Long? = null,
    val uid: String,
)

@Entity(tableName = "sets", indices = [Index("workoutId")])
data class SetRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val workoutId: Long,
    val exerciseId: Long,
    val at: Long,
    val kg: Double,
    val reps: Int,
    val uid: String,
)

@Entity(tableName = "tasks")
data class TaskRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val title: String,
    /** yyyy-MM-dd или null — «Потом». */
    val date: String? = null,
    /** HH:mm или null. */
    val time: String? = null,
    val done: Boolean = false,
    val doneAt: Long? = null,
    val carry: Int = 0,
    val createdAt: Long,
    val uid: String,
)

@Entity(tableName = "notes", indices = [Index("at")])
data class NoteRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val at: Long,
    val text: String,
    val audioPath: String? = null,
    val audioMs: Long? = null,
    /** text / voice / moment */
    val kind: String = "text",
    val uid: String,
)

/** Решение человека о сне: подтвердил дневной, отверг или поправил границы. */
@Entity(tableName = "sleep_marks", indices = [Index("start")])
data class SleepMarkRow(@PrimaryKey(autoGenerate = true) val id: Long = 0, val start: Long, val end: Long, val kind: String)

/** Флаги дня: skip_food, food_reminded, weight_dismissed. key = "yyyy-MM-dd|flag". */
@Entity(tableName = "day_flags")
data class DayFlagRow(@PrimaryKey val key: String, val value: String = "1")

@Entity(tableName = "app_usage", indices = [Index("start")])
data class UsageRow(@PrimaryKey(autoGenerate = true) val id: Long = 0, val pkg: String, val start: Long, val end: Long)

/** Снимки радиоокружения для отпечатков мест. */
@Entity(tableName = "radio", indices = [Index("time")])
data class RadioRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val time: Long,
    val wifi: String,
    val cells: String,
    val connected: String?,
    val hotspot: Boolean,
    val lat: Double?,
    val lon: Double?,
)
