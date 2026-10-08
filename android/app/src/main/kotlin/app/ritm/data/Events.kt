package app.ritm.data

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonObjectBuilder
import kotlinx.serialization.json.buildJsonObject
import java.time.Instant
import java.time.ZoneId
import java.util.UUID

/** Запись события для сервера. Время — UTC + смещение пояса в момент события. */
class Events(private val db: RitmDb) {
    suspend fun emit(type: String, at: Long = System.currentTimeMillis(), id: String = UUID.randomUUID().toString(), payload: JsonObjectBuilder.() -> Unit = {}) {
        emitJson(type, at, id, buildJsonObject(payload))
    }

    suspend fun emitJson(type: String, at: Long, id: String, payload: JsonObject) {
        val offset = ZoneId.systemDefault().rules.getOffset(Instant.ofEpochMilli(at)).totalSeconds
        db.outbox().insert(OutboxEvent(id, type, at, offset, payload.toString()))
    }
}

fun newUid(): String = UUID.randomUUID().toString()
