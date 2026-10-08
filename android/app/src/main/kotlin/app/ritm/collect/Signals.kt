package app.ritm.collect

import app.ritm.data.PingRow
import app.ritm.data.Repo
import app.ritm.data.SpanRow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.put

/** Запись интервалов состояний и моментов активности с событиями для сервера. */
class SignalWriter(private val repo: Repo) {
    private val mutex = Mutex()
    private val lastPing = HashMap<String, Long>()

    suspend fun open(kind: String, at: Long = System.currentTimeMillis()) = mutex.withLock {
        if (repo.db.signals().openSpan(kind) != null) return@withLock
        repo.db.signals().insertSpan(SpanRow(kind = kind, start = at, end = null))
        repo.events.emit("span.open", at) { put("kind", kind) }
    }

    suspend fun close(kind: String, at: Long = System.currentTimeMillis()) = mutex.withLock {
        val s = repo.db.signals().openSpan(kind) ?: return@withLock
        repo.db.signals().closeSpan(s.id, maxOf(at, s.start))
        repo.events.emit("span.close", at) { put("kind", kind); put("start", s.start) }
    }

    suspend fun isOpen(kind: String): Boolean = repo.db.signals().openSpan(kind) != null

    /** Момент активности. Не чаще раза в минуту на источник. */
    suspend fun ping(source: String, at: Long = System.currentTimeMillis()) = mutex.withLock {
        val last = lastPing[source] ?: repo.db.signals().lastPing(source) ?: 0
        if (at - last < 60_000) return@withLock
        lastPing[source] = at
        repo.db.signals().insertPing(PingRow(at = at, source = source))
        repo.events.emit("ping", at) { put("source", source) }
    }
}
