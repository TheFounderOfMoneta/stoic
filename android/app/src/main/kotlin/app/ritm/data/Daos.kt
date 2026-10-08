package app.ritm.data

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface OutboxDao {
    @Insert suspend fun insert(e: OutboxEvent)
    @Query("SELECT * FROM outbox WHERE sent = 0 ORDER BY at LIMIT :limit") suspend fun pending(limit: Int): List<OutboxEvent>
    @Query("SELECT COUNT(*) FROM outbox WHERE sent = 0") suspend fun pendingCount(): Int
    @Query("UPDATE outbox SET sent = 1 WHERE id IN (:ids)") suspend fun markSent(ids: List<String>)
    @Query("DELETE FROM outbox WHERE sent = 1 AND at < :before") suspend fun purgeSent(before: Long)
}

@Dao
interface SignalDao {
    @Insert suspend fun insertSteps(s: StepRow)
    @Query("SELECT * FROM steps WHERE `end` > :from ORDER BY start") suspend fun steps(from: Long): List<StepRow>
    @Query("SELECT COALESCE(SUM(count), 0) FROM steps WHERE start >= :from") suspend fun stepsSince(from: Long): Int

    @Insert suspend fun insertPing(p: PingRow)
    @Query("SELECT MAX(at) FROM pings WHERE source = :source") suspend fun lastPing(source: String): Long?
    @Query("SELECT at FROM pings WHERE at >= :from ORDER BY at") suspend fun pings(from: Long): List<Long>

    @Insert suspend fun insertSpan(s: SpanRow): Long
    @Query("UPDATE spans SET `end` = :end WHERE id = :id") suspend fun closeSpan(id: Long, end: Long)
    @Query("SELECT * FROM spans WHERE kind = :kind AND `end` IS NULL ORDER BY start DESC LIMIT 1") suspend fun openSpan(kind: String): SpanRow?
    @Query("SELECT * FROM spans WHERE kind = :kind AND (`end` IS NULL OR `end` > :from) ORDER BY start") suspend fun spans(kind: String, from: Long): List<SpanRow>
    @Query("SELECT * FROM spans WHERE kind LIKE :prefix || '%' AND (`end` IS NULL OR `end` > :from) ORDER BY start") suspend fun spansLike(prefix: String, from: Long): List<SpanRow>

    @Insert suspend fun insertUsage(rows: List<UsageRow>)
    @Insert suspend fun insertRadio(r: RadioRow)
    @Query("SELECT * FROM radio WHERE time >= :from ORDER BY time") suspend fun radio(from: Long): List<RadioRow>

    @Query("DELETE FROM pings WHERE at < :before") suspend fun purgePings(before: Long)
}

@Dao
interface PlaceDao {
    @Insert suspend fun insertFix(f: FixRow)
    @Query("SELECT * FROM fixes WHERE time >= :from ORDER BY time") suspend fun fixes(from: Long): List<FixRow>
    @Query("SELECT * FROM fixes WHERE verdict = 'accepted' AND forPlaces = 1 ORDER BY time DESC LIMIT 1") suspend fun lastGood(): FixRow?

    @Insert suspend fun insertPlace(p: PlaceRow): Long
    @Update suspend fun updatePlace(p: PlaceRow)
    @Delete suspend fun deletePlace(p: PlaceRow)
    @Query("SELECT * FROM places ORDER BY name") fun placesFlow(): Flow<List<PlaceRow>>
    @Query("SELECT * FROM places") suspend fun places(): List<PlaceRow>

    @Insert suspend fun insertStay(s: StayRow)
    @Query("SELECT * FROM stays WHERE start >= :from ORDER BY start") suspend fun stays(from: Long): List<StayRow>

    @Insert suspend fun dismiss(d: DismissedSpot)
    @Query("SELECT * FROM dismissed_spots") suspend fun dismissed(): List<DismissedSpot>
}

@Dao
interface FoodDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun insertProducts(p: List<ProductRow>)
    @Insert suspend fun insertProduct(p: ProductRow): Long
    @Update suspend fun updateProduct(p: ProductRow)
    @Query("SELECT COUNT(*) FROM products") suspend fun productCount(): Int
    @Query("SELECT * FROM products WHERE id = :id") suspend fun product(id: Long): ProductRow?
    @Query("SELECT * FROM products WHERE id IN (:ids)") suspend fun products(ids: List<Long>): List<ProductRow>
    @Query("SELECT * FROM products WHERE name LIKE '%' || :q || '%' ORDER BY own DESC, uses DESC, length(name) LIMIT 40") suspend fun search(q: String): List<ProductRow>
    @Query("SELECT * FROM products WHERE uses > 0 ORDER BY usedAt DESC LIMIT 30") suspend fun recent(): List<ProductRow>

    @Insert suspend fun insertCombo(c: ComboRow): Long
    @Update suspend fun updateCombo(c: ComboRow)
    @Query("SELECT * FROM combos ORDER BY usedAt DESC") suspend fun combos(): List<ComboRow>
    @Query("SELECT * FROM combos WHERE id = :id") suspend fun combo(id: Long): ComboRow?

    @Insert suspend fun insertFood(f: FoodRow): Long
    @Update suspend fun updateFood(f: FoodRow)
    @Delete suspend fun deleteFood(f: FoodRow)
    @Query("SELECT * FROM food WHERE at >= :from AND at < :to ORDER BY at") suspend fun food(from: Long, to: Long): List<FoodRow>
    @Query("SELECT * FROM food WHERE at >= :from ORDER BY at") fun foodFlow(from: Long): Flow<List<FoodRow>>

    @Insert suspend fun insertWeight(w: WeightRow): Long
    @Delete suspend fun deleteWeight(w: WeightRow)
    @Query("SELECT * FROM weights ORDER BY at DESC LIMIT 1") suspend fun lastWeight(): WeightRow?
    @Query("SELECT * FROM weights WHERE at >= :from ORDER BY at") suspend fun weights(from: Long): List<WeightRow>
}

@Dao
interface WorkoutDao {
    @Insert suspend fun insertWorkout(w: WorkoutRow): Long
    @Update suspend fun updateWorkout(w: WorkoutRow)
    @Delete suspend fun deleteWorkout(w: WorkoutRow)
    @Query("SELECT * FROM workouts WHERE `end` IS NULL ORDER BY start DESC LIMIT 1") suspend fun active(): WorkoutRow?
    @Query("SELECT * FROM workouts WHERE `end` IS NULL ORDER BY start DESC LIMIT 1") fun activeFlow(): Flow<WorkoutRow?>
    @Query("SELECT * FROM workouts WHERE start >= :from ORDER BY start") suspend fun workouts(from: Long): List<WorkoutRow>

    @Insert suspend fun insertSet(s: SetRow): Long
    @Update suspend fun updateSet(s: SetRow)
    @Delete suspend fun deleteSet(s: SetRow)
    @Query("SELECT * FROM sets WHERE workoutId = :workoutId ORDER BY at") fun setsFlow(workoutId: Long): Flow<List<SetRow>>
    @Query("SELECT * FROM sets WHERE workoutId = :workoutId ORDER BY at") suspend fun sets(workoutId: Long): List<SetRow>
    @Query("SELECT * FROM sets WHERE exerciseId = :exerciseId ORDER BY at DESC LIMIT 1") suspend fun lastSet(exerciseId: Long): SetRow?

    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun insertExercise(e: ExerciseRow): Long
    @Update suspend fun updateExercise(e: ExerciseRow)
    @Query("SELECT * FROM exercises WHERE name = :name") suspend fun exercise(name: String): ExerciseRow?
    @Query("SELECT * FROM exercises WHERE id = :id") suspend fun exerciseById(id: Long): ExerciseRow?
    @Query("SELECT * FROM exercises ORDER BY uses DESC, usedAt DESC") fun exercisesFlow(): Flow<List<ExerciseRow>>
}

@Dao
interface TaskDao {
    @Insert suspend fun insert(t: TaskRow): Long
    @Update suspend fun update(t: TaskRow)
    @Delete suspend fun delete(t: TaskRow)
    @Query("SELECT * FROM tasks WHERE done = 0 ORDER BY date IS NULL, date, time IS NULL, time, createdAt") fun openFlow(): Flow<List<TaskRow>>
    @Query("SELECT * FROM tasks WHERE done = 0") suspend fun open(): List<TaskRow>
    @Query("SELECT * FROM tasks WHERE id = :id") suspend fun byId(id: Long): TaskRow?
}

@Dao
interface NoteDao {
    @Insert suspend fun insert(n: NoteRow): Long
    @Update suspend fun update(n: NoteRow)
    @Delete suspend fun delete(n: NoteRow)
    @Query("SELECT * FROM notes ORDER BY at DESC") fun allFlow(): Flow<List<NoteRow>>
    @Query("SELECT * FROM notes WHERE id = :id") suspend fun byId(id: Long): NoteRow?
}

@Dao
interface DayDao {
    @Insert suspend fun insertMark(m: SleepMarkRow)
    @Query("SELECT * FROM sleep_marks WHERE `end` > :from") suspend fun marks(from: Long): List<SleepMarkRow>
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun setFlag(f: DayFlagRow)
    @Query("SELECT value FROM day_flags WHERE `key` = :key") suspend fun flag(key: String): String?
    @Query("SELECT * FROM day_flags WHERE `key` LIKE :prefix || '%'") suspend fun flags(prefix: String): List<DayFlagRow>
}
