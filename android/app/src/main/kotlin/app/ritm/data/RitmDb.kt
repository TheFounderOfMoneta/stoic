package app.ritm.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [
        OutboxEvent::class, StepRow::class, PingRow::class, SpanRow::class, FixRow::class, PlaceRow::class,
        StayRow::class, DismissedSpot::class, ProductRow::class, ComboRow::class, FoodRow::class, WeightRow::class,
        ExerciseRow::class, WorkoutRow::class, SetRow::class, TaskRow::class, NoteRow::class, SleepMarkRow::class,
        DayFlagRow::class, UsageRow::class, RadioRow::class,
    ],
    version = 1,
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
        fun create(context: Context): RitmDb =
            Room.databaseBuilder(context, RitmDb::class.java, "ritm.db").build()
    }
}
