package app.ritm.collect

import android.content.Context
import app.ritm.app
import app.ritm.core.geo.LatLon
import app.ritm.data.DismissedSpot
import app.ritm.data.PlaceRow
import app.ritm.engine.PendingPlace
import kotlinx.serialization.json.put

/** Сохранение мест: таблица, геозоны, отпечаток окружения, события. */
object PlacesActions {
    suspend fun savePending(context: Context, p: PendingPlace, name: String) {
        val repo = context.app.repo
        val now = System.currentTimeMillis()
        val id = repo.db.places().insertPlace(PlaceRow(name = name.trim(), lat = p.lat, lon = p.lon, createdAt = now))
        repo.settings.setPendingPlace("")
        repo.events.emit("place.add", now) { put("id", id); put("name", name.trim()); put("lat", p.lat); put("lon", p.lon); put("radius", 200.0) }
        SignalWriter(repo).open("place:$id", now)
        LocationCollector.learnHere(context, id, LatLon(p.lat, p.lon))
        LocationCollector.refreshGeofences(context)
        context.app.day.refresh()
    }

    suspend fun dismissPending(context: Context, p: PendingPlace) {
        val repo = context.app.repo
        repo.db.places().dismiss(DismissedSpot(lat = p.lat, lon = p.lon))
        repo.settings.setPendingPlace("")
        repo.events.emit("place.dismiss") { put("lat", p.lat); put("lon", p.lon) }
        context.app.day.refresh()
    }

    suspend fun update(context: Context, p: PlaceRow) {
        val repo = context.app.repo
        repo.db.places().updatePlace(p)
        repo.events.emit("place.update") { put("id", p.id); put("name", p.name); put("radius", p.radius) }
        LocationCollector.refreshGeofences(context)
    }

    suspend fun delete(context: Context, p: PlaceRow) {
        val repo = context.app.repo
        repo.db.places().deletePlace(p)
        SignalWriter(repo).close("place:${p.id}")
        repo.events.emit("place.delete") { put("id", p.id) }
        LocationCollector.refreshGeofences(context)
    }
}
