package app.ritm.data

import android.content.Context
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.doublePreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import app.ritm.core.energy.Body
import app.ritm.core.energy.GoalType
import app.ritm.core.energy.Sex
import app.ritm.core.energy.WeightGoal
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.time.LocalDate

private val Context.store by preferencesDataStore("ritm")

/** Профиль и настройки. Всё, что меняют раз в жизни, — здесь. */
data class Prefs(
    val onboarded: Boolean = false,
    val goals: String = "",
    val sex: Sex? = null,
    val heightCm: Double? = null,
    val birthDate: LocalDate? = null,
    val goalType: GoalType = GoalType.KEEP,
    val paceKgPerWeek: Double = 0.5,
    val serverUrl: String = "",
    val serverToken: String = "",
    val bixbyKeyCodes: Set<Int> = DEFAULT_BIXBY_KEYS,
    val showOnLockScreen: Boolean = true,
    val restSeconds: Int = 90,
    val calibration: Double = 1.0,
    val calibrated: Boolean = false,
    val installedAt: Long = 0,
    val lastSyncAt: Long = 0,
    val pendingPlace: String = "",
) {
    val body: Body? get() = if (sex != null && heightCm != null && birthDate != null) Body(sex, heightCm, birthDate) else null
    val goal: WeightGoal get() = WeightGoal(goalType, paceKgPerWeek)

    companion object {
        /** Коды кнопки Bixby на Samsung (уточняются обучением на телефоне). */
        val DEFAULT_BIXBY_KEYS = setOf(1082, 703)
    }
}

class Settings(private val context: Context) {
    private object K {
        val onboarded = booleanPreferencesKey("onboarded")
        val goals = stringPreferencesKey("goals")
        val sex = stringPreferencesKey("sex")
        val height = doublePreferencesKey("height")
        val birth = stringPreferencesKey("birth")
        val goalType = stringPreferencesKey("goal_type")
        val pace = doublePreferencesKey("pace")
        val serverUrl = stringPreferencesKey("server_url")
        val serverToken = stringPreferencesKey("server_token")
        val bixbyKeys = stringPreferencesKey("bixby_keys")
        val showOnLock = booleanPreferencesKey("show_on_lock")
        val rest = intPreferencesKey("rest_seconds")
        val calibration = doublePreferencesKey("calibration")
        val calibrated = booleanPreferencesKey("calibrated")
        val installedAt = longPreferencesKey("installed_at")
        val lastSync = longPreferencesKey("last_sync")
        val pendingPlace = stringPreferencesKey("pending_place")
    }

    val flow: Flow<Prefs> = context.store.data.map(::read)

    suspend fun get(): Prefs = flow.first()

    private fun read(p: Preferences) = Prefs(
        onboarded = p[K.onboarded] ?: false,
        goals = p[K.goals] ?: "",
        sex = p[K.sex]?.let { runCatching { Sex.valueOf(it) }.getOrNull() },
        heightCm = p[K.height],
        birthDate = p[K.birth]?.let { runCatching { LocalDate.parse(it) }.getOrNull() },
        goalType = p[K.goalType]?.let { runCatching { GoalType.valueOf(it) }.getOrNull() } ?: GoalType.KEEP,
        paceKgPerWeek = p[K.pace] ?: 0.5,
        serverUrl = p[K.serverUrl] ?: "",
        serverToken = p[K.serverToken] ?: "",
        bixbyKeyCodes = p[K.bixbyKeys]?.split(',')?.mapNotNull { it.trim().toIntOrNull() }?.toSet()?.takeIf { it.isNotEmpty() }
            ?: Prefs.DEFAULT_BIXBY_KEYS,
        showOnLockScreen = p[K.showOnLock] ?: true,
        restSeconds = p[K.rest] ?: 90,
        calibration = p[K.calibration] ?: 1.0,
        calibrated = p[K.calibrated] ?: false,
        installedAt = p[K.installedAt] ?: 0,
        lastSyncAt = p[K.lastSync] ?: 0,
        pendingPlace = p[K.pendingPlace] ?: "",
    )

    suspend fun ensureInstalledAt(now: Long) = context.store.edit { if (it[K.installedAt] == null) it[K.installedAt] = now }
    suspend fun setOnboarded(v: Boolean) = context.store.edit { it[K.onboarded] = v }
    suspend fun setGoals(v: String) = context.store.edit { it[K.goals] = v }
    suspend fun setBody(sex: Sex, heightCm: Double, birth: LocalDate) = context.store.edit {
        it[K.sex] = sex.name; it[K.height] = heightCm; it[K.birth] = birth.toString()
    }
    suspend fun setGoal(type: GoalType, pace: Double) = context.store.edit { it[K.goalType] = type.name; it[K.pace] = pace }
    suspend fun setServer(url: String, token: String) = context.store.edit { it[K.serverUrl] = url.trim(); it[K.serverToken] = token.trim() }
    suspend fun setBixbyKeys(codes: Set<Int>) = context.store.edit { it[K.bixbyKeys] = codes.joinToString(",") }
    suspend fun setShowOnLock(v: Boolean) = context.store.edit { it[K.showOnLock] = v }
    suspend fun setRestSeconds(v: Int) = context.store.edit { it[K.rest] = v }
    suspend fun setCalibration(factor: Double, calibrated: Boolean) = context.store.edit { it[K.calibration] = factor; it[K.calibrated] = calibrated }
    suspend fun setLastSync(at: Long) = context.store.edit { it[K.lastSync] = at }
    suspend fun setPendingPlace(json: String) = context.store.edit { it[K.pendingPlace] = json }
}
