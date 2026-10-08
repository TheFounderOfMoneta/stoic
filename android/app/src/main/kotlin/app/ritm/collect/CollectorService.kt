package app.ritm.collect

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.BatteryManager
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import app.ritm.app
import app.ritm.button.LogcatKeySource
import app.ritm.core.steps.StepCounterTracker
import app.ritm.data.DayFlagRow
import app.ritm.data.StepRow
import app.ritm.engine.Access
import app.ritm.engine.Notifications
import app.ritm.engine.Permissions
import app.ritm.engine.WorkoutClock
import app.ritm.work.Work
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.put
import kotlin.coroutines.resume

/**
 * Постоянная фоновая служба: держит подписку на счётчик шагов (иначе он не считает),
 * слушает экран, разблокировки и зарядку, раз в 5 минут пересчитывает день.
 */
class CollectorService : LifecycleService() {
    private lateinit var writer: SignalWriter
    private var stepListener: SensorEventListener? = null
    private val tracker = StepCounterTracker()
    private var tickJob: Job? = null
    private var lastLightAt = 0L
    private var lastTickSteps = 0L

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val now = System.currentTimeMillis()
            lifecycleScope.launch {
                when (intent.action) {
                    Intent.ACTION_SCREEN_ON -> { writer.open("screen", now); maybeSync() }
                    Intent.ACTION_SCREEN_OFF -> writer.close("screen", now)
                    Intent.ACTION_USER_PRESENT -> { writer.ping("unlock", now); app.day.refresh() }
                    Intent.ACTION_POWER_CONNECTED -> { writer.open("charging", now); Work.syncNow(this@CollectorService, unmeteredOnly = true) }
                    Intent.ACTION_POWER_DISCONNECTED -> writer.close("charging", now)
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        writer = SignalWriter(app.repo)
        goForeground()
        registerReceiver(receiver, IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_SCREEN_OFF); addAction(Intent.ACTION_USER_PRESENT)
            addAction(Intent.ACTION_POWER_CONNECTED); addAction(Intent.ACTION_POWER_DISCONNECTED)
        })
        lifecycleScope.launch { initialState() }
        startSteps()
        startTicks()
        LogcatKeySource.start(this)
        observeSummary()
        scheduleWatchdogAlarm(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_TICK) lifecycleScope.launch { tick() }
        // Запуск из открытого приложения — переподнять службу с микрофоном: тест показал, что тогда голос
        // пишется и при выключенном экране, а без заранее поднятой службы микрофон отдаёт тишину.
        else { runCatching { goForeground() }; LogcatKeySource.start(this) }
        return START_STICKY
    }

    override fun onDestroy() {
        LogcatKeySource.stop()
        runCatching { unregisterReceiver(receiver) }
        stepListener?.let { getSystemService(SensorManager::class.java).unregisterListener(it) }
        super.onDestroy()
    }

    private fun goForeground() {
        var types = ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        if (Permissions.granted(this, Access.LOCATION)) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
        if (Permissions.granted(this, Access.MICROPHONE)) types = types or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        val n = Notifications.summary(this, app.day.state.value, true, null, null)
        startForeground(Notifications.ID_SUMMARY, n, types)
        if (types and ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE != 0 &&
            getSystemService(PowerManager::class.java).isInteractive
        ) micPromotedAt = System.currentTimeMillis()
    }

    private suspend fun initialState() {
        val now = System.currentTimeMillis()
        val bm = getSystemService(BatteryManager::class.java)
        if (bm.isCharging) writer.open("charging", now) else writer.close("charging", now)
        val pm = getSystemService(PowerManager::class.java)
        if (pm.isInteractive) writer.open("screen", now) else writer.close("screen", now)
        app.repo.db.days().flag("steps|tracker")?.split(',')?.let { p ->
            tracker.lastValue = p.getOrNull(0)?.toLongOrNull(); tracker.lastTime = p.getOrNull(1)?.toLongOrNull()
        }
    }

    // ——— Шаги: аппаратный счётчик, пакетная доставка раз в 10 минут ———

    private fun startSteps() {
        if (!Permissions.granted(this, Access.ACTIVITY)) return
        val sm = getSystemService(SensorManager::class.java)
        val sensor = sm.getDefaultSensor(Sensor.TYPE_STEP_COUNTER) ?: return
        val l = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) {
                val value = e.values[0].toLong()
                val nowWall = System.currentTimeMillis()
                val at = nowWall - (SystemClock.elapsedRealtimeNanos() - e.timestamp) / 1_000_000
                val boot = nowWall - SystemClock.elapsedRealtime()
                lifecycleScope.launch { onSteps(value, at, boot) }
            }
            override fun onAccuracyChanged(s: Sensor?, a: Int) {}
        }
        sm.registerListener(l, sensor, SensorManager.SENSOR_DELAY_NORMAL, 10 * 60 * 1_000_000)
        stepListener = l
    }

    private suspend fun onSteps(value: Long, at: Long, boot: Long) {
        val repo = app.repo
        val interval = synchronized(tracker) { tracker.onReading(value, at, boot) }
        repo.db.days().setFlag(DayFlagRow("steps|tracker", "${tracker.lastValue},${tracker.lastTime}"))
        if (interval != null) {
            repo.db.signals().insertSteps(StepRow(start = interval.interval.start, end = interval.interval.end, count = interval.count))
            repo.events.emit("steps", interval.interval.end) { put("start", interval.interval.start); put("count", interval.count) }
        }
    }

    // ——— Тик раз в 5 минут ———

    private fun startTicks() {
        tickJob = lifecycleScope.launch {
            while (isActive) {
                tick()
                delay(5 * 60_000L)
            }
        }
    }

    private suspend fun tick() {
        val now = System.currentTimeMillis()
        lastTickAt = now
        val repo = app.repo
        getSystemService(SensorManager::class.java).getDefaultSensor(Sensor.TYPE_STEP_COUNTER)?.let {
            // Принудительно выгрузить накопленные шаги из буфера датчика.
            stepListener?.let { l -> getSystemService(SensorManager::class.java).flush(l) }
        }
        scheduleWatchdogAlarm(this)
        val stepsTotal = repo.db.signals().stepsSince(now - 24 * 3600_000L).toLong()
        val delta = if (lastTickSteps == 0L) 0 else (stepsTotal - lastTickSteps).coerceAtLeast(0).toInt()
        lastTickSteps = stepsTotal
        LocationCollector.onTick(this, now, delta)
        sampleLightIfQuiet(now)
        app.day.refresh()
    }

    /** Подозрение на сон (30 мин без активности, экран выключен) → раз в 30 минут посмотреть на свет. */
    private suspend fun sampleLightIfQuiet(now: Long) {
        val repo = app.repo
        val lastActive = repo.db.signals().pings(now - 3600_000L).lastOrNull() ?: 0
        val screenOn = getSystemService(PowerManager::class.java).isInteractive
        if (screenOn || now - lastActive < 30 * 60_000L || now - lastLightAt < 30 * 60_000L) return
        lastLightAt = now
        val lux = readLight() ?: return
        if (lux < 3f) writer.open("dark", now) else writer.close("dark", now)
    }

    private suspend fun readLight(): Float? {
        val sm = getSystemService(SensorManager::class.java)
        val sensor = sm.getDefaultSensor(Sensor.TYPE_LIGHT) ?: return null
        return withTimeoutOrNull(5_000) {
            suspendCancellableCoroutine { cont ->
                val l = object : SensorEventListener {
                    override fun onSensorChanged(e: SensorEvent) {
                        sm.unregisterListener(this)
                        if (cont.isActive) cont.resume(e.values[0])
                    }
                    override fun onAccuracyChanged(s: Sensor?, a: Int) {}
                }
                sm.registerListener(l, sensor, SensorManager.SENSOR_DELAY_NORMAL)
                cont.invokeOnCancellation { sm.unregisterListener(l) }
            }
        }
    }

    private suspend fun maybeSync() {
        val last = app.repo.settings.get().lastSyncAt
        if (System.currentTimeMillis() - last > 30 * 60_000L) Work.syncNow(this)
    }

    /** Шторка следует за днём и тренировкой. */
    private fun observeSummary() {
        lifecycleScope.launch {
            combine(app.day.state, app.repo.activeWorkout(), WorkoutClock.rest, app.repo.settings.flow) { s, w, r, p ->
                Notifications.summary(this@CollectorService, s, p.showOnLockScreen, w?.start, r?.endsAt)
            }.collect { Notifications.updateSummary(this@CollectorService, it) }
        }
    }

    companion object {
        const val ACTION_TICK = "app.ritm.TICK"
        @Volatile var lastTickAt = 0L
        /** Когда служба последний раз поднималась с микрофоном при открытом приложении. */
        @Volatile var micPromotedAt = 0L

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, CollectorService::class.java))
        }

        /** Будильник-страховка раз в 15 минут: поднимает службу, если Samsung её усыпил. */
        fun scheduleWatchdogAlarm(context: Context) {
            val am = context.getSystemService(AlarmManager::class.java)
            val pi = PendingIntent.getForegroundService(
                context, 77, Intent(context, CollectorService::class.java).setAction(ACTION_TICK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val at = System.currentTimeMillis() + 15 * 60_000L
            if (am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
            else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        }
    }
}
