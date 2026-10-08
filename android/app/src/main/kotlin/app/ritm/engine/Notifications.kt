package app.ritm.engine

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import app.ritm.R
import app.ritm.data.TaskRow
import app.ritm.receivers.ActionReceiver
import app.ritm.ui.MainActivity
import app.ritm.ui.QuickActivity
import kotlin.math.abs

object Notifications {
    const val CH_SUMMARY = "summary"
    const val CH_REMIND = "remind"
    const val CH_ALERT = "alert"
    const val ID_SUMMARY = 1
    const val ID_FOOD = 2
    const val ID_ALERT = 3
    private const val ID_TASK_BASE = 10_000

    fun channels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannels(listOf(
            NotificationChannel(CH_SUMMARY, "Сводка дня", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Остаток калорий и отдых на тренировке"; setShowBadge(false)
            },
            NotificationChannel(CH_REMIND, "Напоминания", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Задачи со временем и забытая еда"
            },
            NotificationChannel(CH_ALERT, "Сбор данных", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Когда что-то перестало собираться"
            },
        ))
    }

    private fun openApp(context: Context): PendingIntent = PendingIntent.getActivity(
        context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun action(context: Context, action: String, code: Int, extra: Long = 0): PendingIntent = PendingIntent.getBroadcast(
        context, code, Intent(context, ActionReceiver::class.java).setAction(action).putExtra(ActionReceiver.EXTRA_ID, extra),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun quick(context: Context, screen: String, code: Int): PendingIntent = PendingIntent.getActivity(
        context, code, QuickActivity.intent(context, screen),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    /** Шторка: одна цифра. На тренировке — отдых и кнопка «Подход». */
    fun summary(context: Context, state: DayState, showOnLock: Boolean, workoutStart: Long?, restEndsAt: Long?): Notification {
        val b = NotificationCompat.Builder(context, CH_SUMMARY)
            .setSmallIcon(R.drawable.ic_stat)
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .setOnlyAlertOnce(true)
            .setContentIntent(openApp(context))
            .setVisibility(if (showOnLock) NotificationCompat.VISIBILITY_PUBLIC else NotificationCompat.VISIBILITY_SECRET)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
        if (workoutStart != null) {
            val now = System.currentTimeMillis()
            if (restEndsAt != null && restEndsAt > now) {
                b.setContentTitle("Отдых").setUsesChronometer(true).setChronometerCountDown(true).setWhen(restEndsAt).setShowWhen(true)
            } else {
                b.setContentTitle("Тренировка").setUsesChronometer(true).setWhen(workoutStart).setShowWhen(true)
            }
            b.addAction(0, "Подход", action(context, ActionReceiver.SET, 1))
            b.setContentIntent(quick(context, QuickActivity.WORKOUT, 1))
        } else {
            b.setContentTitle(kcalTitle(state))
        }
        return b.build()
    }

    fun kcalTitle(state: DayState): String {
        val r = state.remaining ?: return "Ритм"
        return if (r >= 0) "${formatInt(r)} ккал" else "сверх ${formatInt(abs(r))} ккал"
    }

    fun updateSummary(context: Context, n: Notification) {
        if (NotificationManagerCompat.from(context).areNotificationsEnabled()) {
            context.getSystemService(NotificationManager::class.java).notify(ID_SUMMARY, n)
        }
    }

    fun foodReminder(context: Context) {
        val n = NotificationCompat.Builder(context, CH_REMIND)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Еда не записана")
            .setContentIntent(quick(context, QuickActivity.FOOD, 2))
            .setAutoCancel(true)
            .addAction(0, "Записать", quick(context, QuickActivity.FOOD, 3))
            .addAction(0, "Сегодня не ем", action(context, ActionReceiver.SKIP_FOOD, 4))
            .build()
        notify(context, ID_FOOD, n)
    }

    fun task(context: Context, t: TaskRow) {
        val n = NotificationCompat.Builder(context, CH_REMIND)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(listOfNotNull(t.time, t.title).joinToString(" "))
            .setContentIntent(openApp(context))
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .addAction(0, "Сделано", action(context, ActionReceiver.TASK_DONE, 100 + t.id.toInt(), t.id))
            .addAction(0, "Через час", action(context, ActionReceiver.TASK_SNOOZE, 200 + t.id.toInt(), t.id))
            .build()
        notify(context, ID_TASK_BASE + t.id.toInt(), n)
    }

    fun cancelTask(context: Context, id: Long) = NotificationManagerCompat.from(context).cancel(ID_TASK_BASE + id.toInt())

    fun alert(context: Context, title: String) {
        val n = NotificationCompat.Builder(context, CH_ALERT)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title)
            .setContentIntent(openApp(context))
            .setAutoCancel(true)
            .build()
        notify(context, ID_ALERT, n)
    }

    fun cancel(context: Context, id: Int) = NotificationManagerCompat.from(context).cancel(id)

    private fun notify(context: Context, id: Int, n: Notification) {
        if (NotificationManagerCompat.from(context).areNotificationsEnabled()) {
            context.getSystemService(NotificationManager::class.java).notify(id, n)
        }
    }
}

/** 1 380 — с тонким пробелом между разрядами. */
fun formatInt(v: Int): String {
    val s = abs(v).toString().reversed().chunked(3).joinToString(" ").reversed()
    return if (v < 0) "−$s" else s
}
