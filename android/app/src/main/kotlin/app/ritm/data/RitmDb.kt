package app.ritm.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [
        OutboxEvent::class, StepRow::class, PingRow::class, SpanRow::class, FixRow::class, PlaceRow::class,
        StayRow::class, DismissedSpot::class, ProductRow::class, ComboRow::class, FoodRow::class, WeightRow::class,
        ExerciseRow::class, WorkoutRow::class, SetRow::class, TaskRow::class, NoteRow::class, SleepMarkRow::class,
        DayFlagRow::class, UsageRow::class, RadioRow::class,
    ],
    version = 2,
    exportSchema = true,
)
abstract class RitmDb : RoomDatabase() {
    abstract fun outbox(): OutboxDao
    abstract fun signals(): SignalDao
    abstract fun places(): PlaceDao
    abstract fun food(): FoodDao
    abstract fun workouts(): WorkoutDao
    abstract fun tasks(): TaskDao
    abstract fun notes(): NoteDao
    abstract fun days(): DayDao

    companion object {
        const val NAME = "ritm.db"

        /**
         * Миграции: каждое изменение таблиц — новая версия и шаг миграции здесь.
         * Без этого обновление приложения упало бы, а данные пропали.
         */
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE places ADD COLUMN isGym INTEGER NOT NULL DEFAULT 0")
                db.execSQL("ALTER TABLE products ADD COLUMN barcode TEXT")
                db.execSQL("UPDATE places SET isGym = 1 WHERE name LIKE '%зал%' OR name LIKE '%Зал%' OR lower(name) LIKE '%gym%'")
            }
        }

        fun create(context: Context): RitmDb =
            Room.databaseBuilder(context, RitmDb::class.java, NAME).addMigrations(MIGRATION_1_2).build()
    }
}
