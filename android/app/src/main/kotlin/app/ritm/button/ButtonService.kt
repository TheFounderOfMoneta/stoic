package app.ritm.button

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import app.ritm.app
import app.ritm.collect.SignalWriter
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
 * Кнопка слева (Bixby) через спецвозможности, без root:
 * удержание — голосовая заметка; 1 — верхнее действие; 2 — еда; 3 — отметка момента.
 * Заодно — сигнал активности для сна: клики, прокрутка, ввод текста (не включённый экран).
 */
class ButtonService : AccessibilityService() {
    private val handler = Handler(Looper.getMainLooper())
    private val detector = PressGestureDetector()
    private lateinit var recorder: VoiceRecorder
    private lateinit var writer: SignalWriter
    private var keyCodes: Set<Int> = emptySet()
    private var proximityNear = false
    private var proximityListener: SensorEventListener? = null
    private var screenOnAtPress = true
    private var amplitudePoll: Runnable? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        recorder = VoiceRecorder(this)
        writer = SignalWriter(app.repo)
        app.scope.launch { app.repo.settings.flow.collect { keyCodes = it.bixbyKeyCodes } }
        running.value = true
    }

    override fun onDestroy() {
        running.value = false
        if (::recorder.isInitialized) recorder.cancel()
        super.onDestroy()
    }

    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (event.packageName == packageName && event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        app.scope.launch { writer.ping("touch") }
    }

    override fun onKeyEvent(event: KeyEvent): Boolean {
        val code = event.keyCode
        if (learning.value) {
            if (event.action == KeyEvent.ACTION_UP && code !in SYSTEM_KEYS) {
                learning.value = false
                learned.value = code
                app.scope.launch { app.repo.settings.setBixbyKeys(keyCodes + code) }
                Haptics.double(this)
                return true
            }
            return code !in SYSTEM_KEYS
        }
        if (code !in keyCodes) return false
        val now = event.eventTime
        val gestures = when (event.action) {
            KeyEvent.ACTION_DOWN -> {
                if (event.repeatCount > 0) return true
                screenOnAtPress = getSystemService(PowerManager::class.java).isInteractive
                if (!screenOnAtPress) watchProximity()
                detector.onDown(now)
            }
            KeyEvent.ACTION_UP -> detector.onUp(now)
            else -> emptyList()
        }
        dispatch(gestures)
        scheduleTick()
        return true
    }

    private fun scheduleTick() {
        handler.removeCallbacksAndMessages(TICK_TOKEN)
        val deadline = detector.nextDeadline() ?: return
        val delay = (deadline - android.os.SystemClock.uptimeMillis()).coerceAtLeast(0)
        handler.postAtTime({
            dispatch(detector.onTick(android.os.SystemClock.uptimeMillis()))
            scheduleTick()
        }, TICK_TOKEN, android.os.SystemClock.uptimeMillis() + delay)
    }

    private fun dispatch(gestures: List<Gesture>) {
        if (gestures.isEmpty()) return
        val state = app.day.state.value
        if (shouldIgnorePress(screenOnAtPress, proximityNear, state.workoutActive)) {
            if (recorder.isRecording) recorder.cancel()
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
                if (recorder.start()) {
                    Haptics.short(this)
                    pollAmplitude()
                } else Haptics.long(this)
            }
            is Gesture.HoldEnd -> {
                stopProximity()
                amplitudePoll?.let { handler.removeCallbacks(it) }
                if (!isVoiceNoteValid(g.durationMs)) { recorder.cancel(); continue }
                val result = recorder.stop()
                if (result == null) { Haptics.long(this); continue }
                app.scope.launch {
                    app.repo.addVoiceNote(result.first, result.second)
                    Haptics.double(this@ButtonService)
                }
            }
        }
    }

    private fun single() {
        val s = app.day.state.value
        when (singlePressAction(s.workoutActive, s.weightCard)) {
            SingleAction.WORKOUT_SET -> app.scope.launch {
                val set = app.repo.addSetLikeLast()
                if (set != null) {
                    Haptics.double(this@ButtonService)
                    WorkoutClock.startRest(this@ButtonService, app.repo.settings.get().restSeconds)
                } else {
                    Haptics.short(this@ButtonService)
                    open(QuickActivity.WORKOUT)
                }
                app.day.refresh()
            }
            SingleAction.WEIGHT -> open(QuickActivity.WEIGHT)
            SingleAction.PLUS -> open(QuickActivity.PLUS)
        }
    }

    private fun moment() {
        app.scope.launch {
            app.repo.addMoment()
            Haptics.double(this@ButtonService)
        }
    }

    private fun open(screen: String) {
        Haptics.short(this)
        startActivity(QuickActivity.intent(this, screen).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    private fun pollAmplitude() {
        val r = object : Runnable {
            override fun run() {
                if (!recorder.isRecording) return
                recorder.sampleAmplitude()
                handler.postDelayed(this, 200)
            }
        }
        amplitudePoll = r
        handler.postDelayed(r, 200)
    }

    // ——— Защита от кармана: датчик приближения только на время нажатия при выключенном экране ———

    private fun watchProximity() {
        if (proximityListener != null) return
        val sm = getSystemService(SensorManager::class.java)
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
        proximityListener?.let { getSystemService(SensorManager::class.java).unregisterListener(it) }
        proximityListener = null
        proximityNear = false
    }

    companion object {
        private val TICK_TOKEN = Any()
        private val SYSTEM_KEYS = setOf(
            KeyEvent.KEYCODE_VOLUME_UP, KeyEvent.KEYCODE_VOLUME_DOWN, KeyEvent.KEYCODE_POWER, KeyEvent.KEYCODE_BACK,
            KeyEvent.KEYCODE_HOME, KeyEvent.KEYCODE_APP_SWITCH, KeyEvent.KEYCODE_VOLUME_MUTE,
        )
        /** Режим обучения: следующее нажатие неизвестной кнопки запоминается как кнопка Ритма. */
        val learning = MutableStateFlow(false)
        val learned = MutableStateFlow<Int?>(null)
        val running = MutableStateFlow(false)
    }
}
