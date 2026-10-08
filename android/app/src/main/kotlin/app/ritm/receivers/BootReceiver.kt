package app.ritm.receivers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import app.ritm.app
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
                }
            } finally {
                pending.finish()
            }
        }
    }
}
