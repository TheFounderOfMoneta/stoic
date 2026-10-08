package app.ritm.button

import android.accessibilityservice.AccessibilityService
import android.view.KeyEvent
import android.view.accessibility.AccessibilityEvent
import app.ritm.app
import app.ritm.collect.SignalWriter
import app.ritm.engine.Haptics
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/**
 * Спецвозможности: сигнал активности для сна (клики, прокрутка, ввод) и кнопки, которые система
 * отдаёт спецвозможностям. На Samsung S10 кнопку Bixby система сюда НЕ отдаёт — для неё LogcatKeySource.
 */
class ButtonService : AccessibilityService() {
    private lateinit var writer: SignalWriter
    private var keyCodes: Set<Int> = emptySet()

    override fun onServiceConnected() {
        super.onServiceConnected()
        writer = SignalWriter(app.repo)
        app.scope.launch { app.repo.settings.flow.collect { keyCodes = it.bixbyKeyCodes } }
        running.value = true
    }

    override fun onDestroy() {
        running.value = false
        super.onDestroy()
    }

    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (::writer.isInitialized) app.scope.launch { writer.ping("touch") }
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
        when (event.action) {
            KeyEvent.ACTION_DOWN -> if (event.repeatCount == 0) ButtonBrain.down(this, "спецвозм.", event.eventTime)
            KeyEvent.ACTION_UP -> ButtonBrain.up(this, "спецвозм.", event.eventTime)
        }
        return true
    }

    companion object {
        private val SYSTEM_KEYS = setOf(
            KeyEvent.KEYCODE_VOLUME_UP, KeyEvent.KEYCODE_VOLUME_DOWN, KeyEvent.KEYCODE_POWER, KeyEvent.KEYCODE_BACK,
            KeyEvent.KEYCODE_HOME, KeyEvent.KEYCODE_APP_SWITCH, KeyEvent.KEYCODE_VOLUME_MUTE,
        )
        val learning = MutableStateFlow(false)
        val learned = MutableStateFlow<Int?>(null)
        val running = MutableStateFlow(false)
    }
}
