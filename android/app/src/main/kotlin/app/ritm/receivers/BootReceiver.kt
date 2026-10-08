package app.ritm.receivers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import app.ritm.app
import app.ritm.engine.Access
import app.ritm.engine.Notifications
import app.ritm.engine.Permissions
import app.ritm.collect.CollectorService
import app.ritm.collect.LocationCollector
import app.ritm.work.TaskAlarms
import kotlinx.coroutines.launch

/** После перезагрузки и обновления — поднять сбор, геозоны и будильники задач. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        context.app.scope.launch {
            try {
                if (context.app.repo.settings.get().onboarded) {
                    CollectorService.start(context)
                    LocationCollector.restart(context)
                    TaskAlarms.rescheduleAll(context)
                    // После перезагрузки микрофон в фоне не дают, пока приложение не открыть хотя бы раз.
                    if (Permissions.granted(context, Access.MICROPHONE)) {
                        Notifications.alert(context, "Телефон перезагружен — нажмите, чтобы голос кнопкой снова работал")
                    }
                }
            } finally {
                pending.finish()
            }
        }
    }
}
