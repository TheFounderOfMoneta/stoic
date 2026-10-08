package app.ritm.button

import android.content.Context
import android.content.Intent
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import app.ritm.app
import app.ritm.core.input.Gesture
import app.ritm.core.input.PressGestureDetector
import app.ritm.core.input.SingleAction
import app.ritm.core.input.isVoiceNoteValid
import app.ritm.core.input.shouldIgnorePress
import app.ritm.core.input.singlePressAction
import app.ritm.engine.Haptics
import app.ritm.engine.WorkoutClock
import app.ritm.ui.QuickActivity
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/**
 * Логика кнопки, общая для всех источников нажатий (спецвозможности, системный журнал).
 * Источник сообщает только «нажата / отпущена» во времени uptime; жесты и действия — здесь.
 */
object ButtonBrain {
    private val handler = Handler(Looper.getMainLooper())
    private val detector = PressGestureDetector()
    private var recorder: VoiceRecorder? = null
    private var proximityNear = false
    private var proximityListener: SensorEventListener? = null
    private var screenOnAtPress = true
    private var amplitudePoll: Runnable? = null
    private val tickToken = Any()
    private lateinit var ctx: Context

    /** Последние нажатия для экрана диагностики: «источник · вниз/вверх · время». */
    val recent = MutableStateFlow<List<String>>(emptyList())

    fun down(context: Context, source: String, uptime: Long) = handler.post {
        ctx = context.applicationContext
        log("$source ↓")
        screenOnAtPress = ctx.getSystemService(PowerManager::class.java).isInteractive
        if (!screenOnAtPress) watchProximity()
        dispatch(detector.onDown(uptime))
        scheduleTick()
    }

    fun up(context: Context, source: String, uptime: Long) = handler.post {
        ctx = context.applicationContext
        log("$source ↑")
        dispatch(detector.onUp(uptime))
        scheduleTick()
    }

    private fun log(s: String) {
        val t = java.time.LocalTime.now().withNano(0)
        recent.value = (listOf("$t  $s") + recent.value).take(20)
    }

    private fun scheduleTick() {
        handler.removeCallbacksAndMessages(tickToken)
        val deadline = detector.nextDeadline() ?: return
        handler.postAtTime({
            dispatch(detector.onTick(SystemClock.uptimeMillis()))
            scheduleTick()
        }, tickToken, maxOf(deadline, SystemClock.uptimeMillis()))
    }

    private fun recorder(): VoiceRecorder = recorder ?: VoiceRecorder(ctx).also { recorder = it }

    private fun dispatch(gestures: List<Gesture>) {
        if (gestures.isEmpty()) return
        val app = ctx.app
        val state = app.day.state.value
        if (shouldIgnorePress(screenOnAtPress, proximityNear, state.workoutActive)) {
            recorder().cancel()
            stopProximity()
            return
        }
        for (g in gestures) when (g) {
            is Gesture.Taps -> {
                stopProximity()
                when (g.count) {
                    1 -> single()
                    2 -> open(QuickActivity.FOOD)
                    else -> moment()
                }
            }
            Gesture.HoldStart -> {
                if (recorder().start()) { Haptics.short(ctx); pollAmplitude() } else Haptics.long(ctx)
            }
            is Gesture.HoldEnd -> {
                stopProximity()
                amplitudePoll?.let { handler.removeCallbacks(it) }
                val r = recorder()
                if (!isVoiceNoteValid(g.durationMs)) { r.cancel(); continue }
                val heard = r.heardSound
                val result = r.stop()
                if (result == null) { Haptics.long(ctx); continue }
                app.scope.launch {
                    app.repo.addVoiceNote(result.first, result.second, silent = !heard)
                    // Тишина в записи: микрофон в фоне не дали — длинная вибрация, чтобы не потерять мысль.
                    if (heard) Haptics.double(ctx) else Haptics.long(ctx)
                }
            }
        }
    }

    private fun single() {
        val app = ctx.app
        val s = app.day.state.value
        when (singlePressAction(s.workoutActive, s.weightCard)) {
            SingleAction.WORKOUT_SET -> app.scope.launch {
                val set = app.repo.addSetLikeLast()
                if (set != null) {
                    Haptics.double(ctx)
                    WorkoutClock.startRest(ctx, app.repo.settings.get().restSeconds)
                } else open(QuickActivity.WORKOUT)
                app.day.refresh()
            }
            SingleAction.WEIGHT -> open(QuickActivity.WEIGHT)
            SingleAction.PLUS -> open(QuickActivity.PLUS)
        }
    }

    private fun moment() {
        val app = ctx.app
        app.scope.launch { app.repo.addMoment(); Haptics.double(ctx) }
    }

    private fun open(screen: String) {
        Haptics.short(ctx)
        ctx.startActivity(QuickActivity.intent(ctx, screen).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    private fun pollAmplitude() {
        val r = object : Runnable {
            override fun run() {
                val rec = recorder()
                if (!rec.isRecording) return
                rec.sampleAmplitude()
                handler.postDelayed(this, 200)
            }
        }
        amplitudePoll = r
        handler.postDelayed(r, 200)
    }

    private fun watchProximity() {
        if (proximityListener != null) return
        val sm = ctx.getSystemService(SensorManager::class.java)
        val sensor = sm.getDefaultSensor(Sensor.TYPE_PROXIMITY) ?: return
        proximityNear = false
        val l = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) { proximityNear = e.values[0] < sensor.maximumRange.coerceAtMost(3f) }
            override fun onAccuracyChanged(s: Sensor?, a: Int) {}
        }
        sm.registerListener(l, sensor, SensorManager.SENSOR_DELAY_FASTEST)
        proximityListener = l
    }

    private fun stopProximity() {
        proximityListener?.let { ctx.getSystemService(SensorManager::class.java).unregisterListener(it) }
        proximityListener = null
        proximityNear = false
    }
}
