package app.ritm.collect

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import app.ritm.app
import com.google.android.gms.location.ActivityTransition
import com.google.android.gms.location.ActivityTransitionResult
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingEvent
import com.google.android.gms.location.LocationResult
import kotlinx.coroutines.launch
import kotlinx.serialization.json.put

/** Точки, переходы активности, геозоны и будильник точной точки — работают и когда приложение выгружено. */
class LocationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.app
        val pending = goAsync()
        app.scope.launch {
            try {
                when (intent.action) {
                    LOCATIONS -> LocationResult.extractResult(intent)?.let { LocationCollector.process(context, it.locations) }
                    MOTION -> ActivityTransitionResult.extractResult(intent)?.let { r ->
                        val events = r.transitionEvents.sortedBy { it.elapsedRealTimeNanos }
                        val wallOffset = System.currentTimeMillis() - android.os.SystemClock.elapsedRealtime()
                        LocationCollector.onMotion(
                            context,
                            events.map { it.activityType to (it.transitionType == ActivityTransition.ACTIVITY_TRANSITION_ENTER) },
                            events.map { wallOffset + it.elapsedRealTimeNanos / 1_000_000 },
                        )
                    }
                    GEOFENCE -> GeofencingEvent.fromIntent(intent)?.takeIf { !it.hasError() }?.let { e -> onGeofence(context, e) }
                    PRECISE -> LocationCollector.preciseFix(context)
                }
                app.day.refresh()
            } finally {
                pending.finish()
            }
        }
    }

    private suspend fun onGeofence(context: Context, e: GeofencingEvent) {
        val repo = context.app.repo
        val writer = SignalWriter(repo)
        val now = System.currentTimeMillis()
        val places = repo.db.places().places()
        for (g in e.triggeringGeofences.orEmpty()) {
            val place = places.firstOrNull { it.id.toString() == g.requestId } ?: continue
            val kind = "place:${place.id}"
            val isGym = place.isGym
            when (e.geofenceTransition) {
                Geofence.GEOFENCE_TRANSITION_ENTER -> writer.open(kind, now)
                Geofence.GEOFENCE_TRANSITION_DWELL -> {
                    writer.open(kind, now)
                    // Тренировка начинается сама через 5 минут в зале.
                    if (isGym) repo.startWorkout(auto = true, placeId = place.id)
                }
                Geofence.GEOFENCE_TRANSITION_EXIT -> {
                    writer.close(kind, now)
                    val w = repo.db.workouts().active()
                    if (isGym && w != null && w.placeId == place.id) repo.endWorkout(now)
                }
            }
            repo.events.emit("geofence", now) { put("placeId", place.id); put("transition", e.geofenceTransition) }
        }
    }

    companion object {
        const val LOCATIONS = "app.ritm.LOCATIONS"
        const val MOTION = "app.ritm.MOTION"
        const val GEOFENCE = "app.ritm.GEOFENCE"
        const val PRECISE = "app.ritm.PRECISE"
    }
}
