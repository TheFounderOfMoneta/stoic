package app.ritm.receivers

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import app.ritm.app
import app.ritm.engine.Haptics
import app.ritm.engine.Notifications
import app.ritm.engine.WorkoutClock
import app.ritm.work.TaskAlarms
import kotlinx.coroutines.launch

/** Действия из шторки и будильников. */
class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.app
        val pending = goAsync()
        app.scope.launch {
            try {
                when (intent.action) {
                    SET -> {
                        val s = app.repo.addSetLikeLast()
                        if (s != null) {
                            Haptics.double(context)
                            WorkoutClock.startRest(context, app.repo.settings.get().restSeconds)
                        } else Haptics.long(context)
                    }
                    REST_DONE -> { WorkoutClock.onRestEnded(); Haptics.three(context) }
                    SKIP_FOOD -> {
                        app.day.state.value.date?.let { app.repo.setFlag(it, "skip_food") }
                        Notifications.cancel(context, Notifications.ID_FOOD)
                    }
                    TASK_DONE -> {
                        val id = intent.getLongExtra(EXTRA_ID, 0)
                        app.repo.task(id)?.let { app.repo.completeTask(it) }
                        Notifications.cancelTask(context, id)
                    }
                    TASK_SNOOZE -> {
                        val id = intent.getLongExtra(EXTRA_ID, 0)
                        Notifications.cancelTask(context, id)
                        TaskAlarms.snooze(context, id)
                    }
                    TASK_ALARM -> {
                        val id = intent.getLongExtra(EXTRA_ID, 0)
                        app.repo.task(id)?.takeIf { !it.done }?.let { Notifications.task(context, it) }
                    }
                }
                app.day.refresh()
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        const val SET = "app.ritm.SET"
        const val REST_DONE = "app.ritm.REST_DONE"
        const val SKIP_FOOD = "app.ritm.SKIP_FOOD"
        const val TASK_DONE = "app.ritm.TASK_DONE"
        const val TASK_SNOOZE = "app.ritm.TASK_SNOOZE"
        const val TASK_ALARM = "app.ritm.TASK_ALARM"
        const val EXTRA_ID = "id"
    }
}
