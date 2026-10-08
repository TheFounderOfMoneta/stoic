package app.ritm.engine

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import app.ritm.receivers.ActionReceiver
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** Отдых между подходами: кольцо на экране, отсчёт в шторке и вибрация по окончании. */
object WorkoutClock {
    data class Rest(val startedAt: Long, val endsAt: Long)

    private val _rest = MutableStateFlow<Rest?>(null)
    val rest: StateFlow<Rest?> = _rest

    fun startRest(context: Context, seconds: Int) {
        val now = System.currentTimeMillis()
        val r = Rest(now, now + seconds * 1000L)
        _rest.value = r
        val am = context.getSystemService(AlarmManager::class.java)
        val pi = restIntent(context)
        if (am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.endsAt, pi)
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.endsAt, pi)
    }

    fun clear(context: Context) {
        _rest.value = null
        context.getSystemService(AlarmManager::class.java).cancel(restIntent(context))
    }

    fun onRestEnded() { _rest.value = null }

    private fun restIntent(context: Context): PendingIntent = PendingIntent.getBroadcast(
        context, 7, Intent(context, ActionReceiver::class.java).setAction(ActionReceiver.REST_DONE),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )
}
