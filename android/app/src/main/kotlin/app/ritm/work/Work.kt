package app.ritm.work

import android.app.AlarmManager
import android.app.PendingIntent
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.os.BatteryManager
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.ritm.BuildConfigProxy
import app.ritm.app
import app.ritm.core.energy.EnergyDay
import app.ritm.core.energy.calibrate
import app.ritm.data.DayFlagRow
import app.ritm.data.PingRow
import app.ritm.data.UsageRow
import app.ritm.data.json
import app.ritm.engine.Access
import app.ritm.engine.DaySummary
import app.ritm.engine.Permissions
import app.ritm.receivers.ActionReceiver
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import java.net.HttpURLConnection
import java.net.URL
import java.time.LocalDate
import java.util.concurrent.TimeUnit

object Work {
    fun schedule(context: Context) {
        val wm = WorkManager.getInstance(context)
        wm.enqueueUniquePeriodicWork("sync", ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<SyncWorker>(60, TimeUnit.MINUTES).build())
        wm.enqueueUniquePeriodicWork("usage", ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<UsageWorker>(60, TimeUnit.MINUTES).build())
    }

    /** Отправить сейчас (экран включён, или на зарядке с Wi-Fi). */
    fun syncNow(context: Context, unmeteredOnly: Boolean = false) {
        val c = Constraints.Builder().setRequiredNetworkType(if (unmeteredOnly) NetworkType.UNMETERED else NetworkType.CONNECTED).build()
        WorkManager.getInstance(context).enqueueUniqueWork("sync-now", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<SyncWorker>().setConstraints(c).build())
    }
}

/**
 * Раз в час: сигнал «жив» + отправка накопленного на сервер, итоги закрытых дней, калибровка раз в сутки.
 */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext.app
        val repo = app.repo
        runCatching { app.day.summarizeClosedDays() }
        runCatching { dailyCalibration() }
        heartbeat()
        val prefs = repo.settings.get()
        if (prefs.serverUrl.isBlank()) return Result.success()
        return try {
            while (true) {
                val batch = repo.db.outbox().pending(300)
                if (batch.isEmpty()) break
                val body = buildJsonObject {
                    put("device", "phone")
                    put("sentAt", System.currentTimeMillis())
                    putJsonArray("events") {
                        batch.forEach { e ->
                            addJsonObject {
                                put("id", e.id); put("type", e.type); put("at", e.at); put("offsetSec", e.offsetSec)
                                put("payload", json.parseToJsonElement(e.payload))
                            }
                        }
                    }
                }
                if (!post(prefs.serverUrl.trimEnd('/') + "/v1/events", prefs.serverToken, body.toString())) return Result.retry()
                repo.db.outbox().markSent(batch.map { it.id })
            }
            repo.db.outbox().purgeSent(System.currentTimeMillis() - 14L * 24 * 3600_000)
            repo.settings.setLastSync(System.currentTimeMillis())
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }

    private suspend fun heartbeat() {
        val repo = applicationContext.app.repo
        val bm = applicationContext.getSystemService(BatteryManager::class.java)
        val missing = Permissions.missing(applicationContext)
        repo.events.emit("heartbeat") {
            put("battery", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY))
            put("charging", bm.isCharging)
            put("pending", repo.db.outbox().pendingCount())
            putJsonArray("missing") { missing.forEach { add(it.name) } }
            put("version", BuildConfigProxy.versionName(applicationContext))
        }
    }

    private suspend fun dailyCalibration() {
        val repo = applicationContext.app.repo
        val today = LocalDate.now()
        val key = "calibration|$today"
        if (repo.db.days().flag(key) != null) return
        val days = repo.db.days().flags("summary|").mapNotNull { f ->
            runCatching { json.decodeFromString(DaySummary.serializer(), f.value) }.getOrNull()
        }.map { EnergyDay(LocalDate.parse(it.date), it.intake, it.estimated, it.complete) }
        val weights = repo.db.food().weights(System.currentTimeMillis() - 60L * 24 * 3600_000).map { repo.localDate(it.at) to it.kg }
        val c = calibrate(days, weights, today)
        if (c != null) {
            repo.settings.setCalibration(c.factor, true)
            repo.events.emit("calibration") { put("factor", c.factor); put("realTdee", c.realTdee); put("estimatedTdee", c.estimatedTdee); put("days", c.days) }
        }
        repo.db.days().setFlag(DayFlagRow(key))
    }

    private fun post(url: String, token: String, body: String): Boolean {
        val conn = URL(url).openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15_000
            conn.readTimeout = 30_000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            if (token.isNotBlank()) conn.setRequestProperty("Authorization", "Bearer $token")
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            conn.responseCode in 200..299
        } finally {
            conn.disconnect()
        }
    }
}

/** Раз в час: экранное время по приложениям + разблокировки (на случай, если служба кнопки была выключена). */
class UsageWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        if (!Permissions.granted(applicationContext, Access.USAGE)) return Result.success()
        val repo = applicationContext.app.repo
        val usm = applicationContext.getSystemService(UsageStatsManager::class.java)
        val now = System.currentTimeMillis()
        val from = repo.db.days().flag("usage|last")?.toLongOrNull() ?: (now - 24 * 3600_000L)
        val events = usm.queryEvents(from, now)
        val open = HashMap<String, Long>()
        val sessions = mutableListOf<UsageRow>()
        val e = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(e)
            when (e.eventType) {
                UsageEvents.Event.ACTIVITY_RESUMED -> {
                    open.putIfAbsent(e.packageName, e.timeStamp)
                    repo.db.signals().insertPing(PingRow(at = e.timeStamp, source = "usage"))
                }
                UsageEvents.Event.ACTIVITY_PAUSED -> open.remove(e.packageName)?.let { s ->
                    if (e.timeStamp - s >= 1000) sessions += UsageRow(pkg = e.packageName, start = s, end = e.timeStamp)
                }
                UsageEvents.Event.SCREEN_NON_INTERACTIVE -> {
                    open.forEach { (pkg, s) -> if (e.timeStamp - s >= 1000) sessions += UsageRow(pkg = pkg, start = s, end = e.timeStamp) }
                    open.clear()
                }
                UsageEvents.Event.KEYGUARD_HIDDEN -> repo.db.signals().insertPing(PingRow(at = e.timeStamp, source = "unlock"))
            }
        }
        // Незакрытые сессии досчитаем в следующий раз — начинаем с самой ранней открытой.
        val next = open.values.minOrNull() ?: now
        if (sessions.isNotEmpty()) {
            repo.db.signals().insertUsage(sessions)
            repo.events.emitJson("usage", now, java.util.UUID.randomUUID().toString(), buildJsonObject {
                putJsonArray("sessions") {
                    sessions.forEach { s -> addJsonObject { put("pkg", s.pkg); put("start", s.start); put("end", s.end) } }
                }
            })
        }
        repo.db.days().setFlag(DayFlagRow("usage|last", next.toString()))
        return Result.success()
    }
}

/** Будильники задач со временем. */
object TaskAlarms {
    private fun pi(context: Context, id: Long): PendingIntent = PendingIntent.getBroadcast(
        context, 50_000 + id.toInt(),
        Intent(context, ActionReceiver::class.java).setAction(ActionReceiver.TASK_ALARM).putExtra(ActionReceiver.EXTRA_ID, id),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun schedule(context: Context, id: Long, at: Long) {
        if (at <= System.currentTimeMillis()) return
        val am = context.getSystemService(AlarmManager::class.java)
        if (am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi(context, id))
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi(context, id))
    }

    fun cancel(context: Context, id: Long) = context.getSystemService(AlarmManager::class.java).cancel(pi(context, id))

    fun snooze(context: Context, id: Long) = schedule(context, id, System.currentTimeMillis() + 3600_000L)

    fun timeOf(date: String?, time: String?): Long? {
        if (date == null || time == null) return null
        return runCatching {
            LocalDate.parse(date).atTime(java.time.LocalTime.parse(time)).atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli()
        }.getOrNull()
    }

    suspend fun rescheduleAll(context: Context) {
        context.app.repo.db.tasks().open().forEach { t -> timeOf(t.date, t.time)?.let { schedule(context, t.id, it) } }
    }
}

@Suppress("unused")
private fun JsonObject.str(k: String) = (this[k] as? JsonPrimitive)?.content
