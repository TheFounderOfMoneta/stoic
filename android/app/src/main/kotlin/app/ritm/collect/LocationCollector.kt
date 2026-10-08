package app.ritm.collect

import android.annotation.SuppressLint
import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.location.GnssStatus
import android.location.Location
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.Looper
import android.os.SystemClock
import android.telephony.CellInfoGsm
import android.telephony.CellInfoLte
import android.telephony.CellInfoNr
import android.telephony.CellInfoWcdma
import android.telephony.CellIdentityNr
import android.telephony.TelephonyManager
import app.ritm.app
import app.ritm.core.geo.Fix
import app.ritm.core.geo.LatLon
import app.ritm.core.geo.LocationFilter
import app.ritm.core.geo.Motion
import app.ritm.core.geo.MotionContext
import app.ritm.core.geo.Provider
import app.ritm.core.geo.RadioScan
import app.ritm.core.geo.Verdict
import app.ritm.core.places.Place
import app.ritm.core.places.Stay
import app.ritm.core.places.StayDetector
import app.ritm.core.places.StayEvent
import app.ritm.core.places.guessPlaceKind
import app.ritm.core.places.placeAt
import app.ritm.core.places.shouldSuggestPlace
import app.ritm.core.time.Interval
import app.ritm.data.FixRow
import app.ritm.data.RadioRow
import app.ritm.data.StayRow
import app.ritm.data.json
import app.ritm.engine.Access
import app.ritm.engine.PendingPlace
import app.ritm.engine.Permissions
import com.google.android.gms.location.ActivityRecognition
import com.google.android.gms.location.ActivityTransition
import com.google.android.gms.location.ActivityTransitionRequest
import com.google.android.gms.location.DetectedActivity
import com.google.android.gms.location.Geofence
import com.google.android.gms.location.GeofencingRequest
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.put
import java.util.concurrent.Executors
import kotlin.math.sqrt

/**
 * Геопозиция с минимальной погрешностью и минимальным расходом:
 * — в движении экономичный режим раз в 5 минут;
 * — через 5 минут после остановки одна точная точка (лучшая за 30 секунд, а не первая);
 * — на месте ничего, кроме пассивных точек чужих приложений;
 * — свои места — системные геозоны;
 * — каждая точка проходит фильтр подмены/глушения.
 */
object LocationCollector {
    private const val MOVING_INTERVAL = 5 * 60_000L
    private const val PRECISE_DELAY = 5 * 60_000L
    private const val PRECISE_WINDOW = 30_000L
    private const val PRECISE_GOOD_ENOUGH_M = 15f

    private val mutex = Mutex()
    private var filter: LocationFilter? = null
    private val stays = StayDetector()
    private var book: app.ritm.core.geo.FingerprintBook? = null

    private fun pi(context: Context, action: String, code: Int): PendingIntent = PendingIntent.getBroadcast(
        context, code, Intent(context, LocationReceiver::class.java).setAction(action),
        PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun movingIntent(c: Context) = pi(c, LocationReceiver.LOCATIONS, 31)
    private fun passiveIntent(c: Context) = pi(c, LocationReceiver.LOCATIONS, 32)
    private fun motionIntent(c: Context) = pi(c, LocationReceiver.MOTION, 33)
    private fun geofenceIntent(c: Context) = pi(c, LocationReceiver.GEOFENCE, 34)
    private fun preciseAlarm(c: Context) = PendingIntent.getBroadcast(
        c, 35, Intent(c, LocationReceiver::class.java).setAction(LocationReceiver.PRECISE),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    /** Подписки: переходы активности, пассивные точки, геозоны. */
    @SuppressLint("MissingPermission")
    suspend fun restart(context: Context) {
        if (!Permissions.granted(context, Access.LOCATION)) return
        val fused = LocationServices.getFusedLocationProviderClient(context)
        runCatching {
            fused.requestLocationUpdates(
                LocationRequest.Builder(Priority.PRIORITY_PASSIVE, 10 * 60_000L).setMinUpdateIntervalMillis(60_000L).build(),
                passiveIntent(context),
            )
        }
        if (Permissions.granted(context, Access.ACTIVITY)) runCatching {
            val types = listOf(DetectedActivity.STILL, DetectedActivity.WALKING, DetectedActivity.RUNNING, DetectedActivity.ON_BICYCLE, DetectedActivity.IN_VEHICLE)
            val transitions = types.flatMap { t ->
                listOf(ActivityTransition.ACTIVITY_TRANSITION_ENTER, ActivityTransition.ACTIVITY_TRANSITION_EXIT).map {
                    ActivityTransition.Builder().setActivityType(t).setActivityTransition(it).build()
                }
            }
            ActivityRecognition.getClient(context).requestActivityTransitionUpdates(ActivityTransitionRequest(transitions), motionIntent(context))
        }
        refreshGeofences(context)
    }

    @SuppressLint("MissingPermission")
    suspend fun refreshGeofences(context: Context) {
        if (!Permissions.granted(context, Access.LOCATION)) return
        val client = LocationServices.getGeofencingClient(context)
        runCatching { client.removeGeofences(geofenceIntent(context)) }
        val places = context.app.repo.db.places().places()
        if (places.isEmpty()) return
        val fences = places.map {
            Geofence.Builder()
                .setRequestId(it.id.toString())
                .setCircularRegion(it.lat, it.lon, it.radius.toFloat())
                .setExpirationDuration(Geofence.NEVER_EXPIRE)
                .setLoiteringDelay(5 * 60_000)
                .setTransitionTypes(Geofence.GEOFENCE_TRANSITION_ENTER or Geofence.GEOFENCE_TRANSITION_EXIT or Geofence.GEOFENCE_TRANSITION_DWELL)
                .build()
        }
        runCatching {
            client.addGeofences(
                GeofencingRequest.Builder().setInitialTrigger(GeofencingRequest.INITIAL_TRIGGER_ENTER).addGeofences(fences).build(),
                geofenceIntent(context),
            )
        }
    }

    // ——— Переходы активности ———

    suspend fun onMotion(context: Context, events: List<Pair<Int, Boolean>>, times: List<Long>) {
        val writer = SignalWriter(context.app.repo)
        var lastEnter: Int? = null
        events.zip(times).forEach { (e, t) ->
            val (type, enter) = e
            val kind = motionKind(type) ?: return@forEach
            if (enter) { writer.open(kind, t); lastEnter = type } else writer.close(kind, t)
        }
        when (lastEnter) {
            DetectedActivity.STILL -> { stopMoving(context); schedulePrecise(context) }
            null -> {}
            else -> { cancelPrecise(context); startMoving(context) }
        }
    }

    private fun motionKind(type: Int): String? = when (type) {
        DetectedActivity.STILL -> "still"
        DetectedActivity.WALKING -> "walking"
        DetectedActivity.RUNNING -> "running"
        DetectedActivity.ON_BICYCLE -> "bicycle"
        DetectedActivity.IN_VEHICLE -> "vehicle"
        else -> null
    }

    @SuppressLint("MissingPermission")
    private fun startMoving(context: Context) {
        if (!Permissions.granted(context, Access.LOCATION)) return
        val req = LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, MOVING_INTERVAL)
            .setMinUpdateIntervalMillis(2 * 60_000L)
            .setMinUpdateDistanceMeters(50f)
            .setMaxUpdateDelayMillis(10 * 60_000L)
            .build()
        runCatching { LocationServices.getFusedLocationProviderClient(context).requestLocationUpdates(req, movingIntent(context)) }
    }

    private fun stopMoving(context: Context) {
        runCatching { LocationServices.getFusedLocationProviderClient(context).removeLocationUpdates(movingIntent(context)) }
    }

    private fun schedulePrecise(context: Context) {
        val am = context.getSystemService(AlarmManager::class.java)
        val at = System.currentTimeMillis() + PRECISE_DELAY
        if (am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, preciseAlarm(context))
        else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, preciseAlarm(context))
    }

    private fun cancelPrecise(context: Context) = context.getSystemService(AlarmManager::class.java).cancel(preciseAlarm(context))

    // ——— Точная точка после остановки ———

    private data class GnssStats(val satellites: Int, val mean: Double, val std: Double)

    /** Лучшая точка за 30 секунд высокой точности + статистика спутников для проверки подмены. */
    @SuppressLint("MissingPermission")
    suspend fun preciseFix(context: Context) {
        if (!Permissions.granted(context, Access.LOCATION)) return
        val fused = LocationServices.getFusedLocationProviderClient(context)
        val lm = context.getSystemService(LocationManager::class.java)
        var best: Location? = null
        var gnss: GnssStats? = null
        val done = CompletableDeferred<Unit>()
        val executor = Executors.newSingleThreadExecutor()
        val gnssCb = object : GnssStatus.Callback() {
            override fun onSatelliteStatusChanged(status: GnssStatus) {
                val used = (0 until status.satelliteCount).filter { status.usedInFix(it) }.map { status.getCn0DbHz(it).toDouble() }
                if (used.size >= 4) {
                    val mean = used.average()
                    gnss = GnssStats(used.size, mean, sqrt(used.sumOf { (it - mean) * (it - mean) } / used.size))
                }
            }
        }
        val cb = object : LocationCallback() {
            override fun onLocationResult(r: LocationResult) {
                r.locations.forEach { l -> if (best == null || l.accuracy < best!!.accuracy) best = l }
                if ((best?.accuracy ?: Float.MAX_VALUE) <= PRECISE_GOOD_ENOUGH_M) done.complete(Unit)
            }
        }
        runCatching { lm.registerGnssStatusCallback(executor, gnssCb) }
        runCatching {
            fused.requestLocationUpdates(
                LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 2_000L).setDurationMillis(PRECISE_WINDOW).build(),
                cb, Looper.getMainLooper(),
            )
        }
        withTimeoutOrNull(PRECISE_WINDOW) { done.await() }
        runCatching { fused.removeLocationUpdates(cb) }
        runCatching { lm.unregisterGnssStatusCallback(gnssCb) }
        executor.shutdown()
        best?.let { process(context, listOf(it), gnss) }
    }

    // ——— Обработка точек ———

    suspend fun process(context: Context, locations: List<Location>) = process(context, locations, null)

    private suspend fun process(context: Context, locations: List<Location>, gnss: GnssStats?) = mutex.withLock {
        val app = context.app
        val repo = app.repo
        val store = LocationStore(context)
        val f = filter ?: LocationFilter(store.loadFilter()).also { filter = it }
        val radio = radioScan(context)
        val places = repo.db.places().places()
        for (loc in locations.sortedBy { it.time }) {
            val fix = toFix(loc, gnss)
            val ctx = motionContext(context, f.state.last?.time ?: (fix.time - 5 * 60_000L), fix.time)
            val verdicts = f.process(fix, ctx, recentNetwork(context, fix.time))
            for (v in verdicts) {
                val (fixes, verdict, usable) = when (v) {
                    is Verdict.Accepted -> Triple(listOf(v.fix), "accepted", v.usableForPlaces)
                    is Verdict.Rejected -> Triple(listOf(v.fix), "rejected:${v.reason.name.lowercase()}", false)
                    is Verdict.Quarantined -> Triple(listOf(v.fix), "quarantined", false)
                    is Verdict.Promoted -> Triple(v.fixes, "promoted", true)
                    is Verdict.Dropped -> Triple(v.fixes, "dropped", false)
                }
                for (x in fixes) {
                    val inPlace = if (usable) placeAt(x.point, places.map { Place(it.id, it.name, LatLon(it.lat, it.lon), it.radius) }) else null
                    repo.db.places().insertFix(FixRow(time = x.time, lat = x.point.lat, lon = x.point.lon, acc = x.accuracyM,
                        provider = x.provider.name, verdict = verdict, forPlaces = usable, placeId = inPlace?.id))
                    repo.events.emit("fix", x.time) {
                        put("lat", x.point.lat); put("lon", x.point.lon); put("acc", x.accuracyM); put("provider", x.provider.name)
                        put("verdict", verdict); inPlace?.let { put("placeId", it.id) }
                    }
                    if (usable) {
                        handleStays(context, stays.onFix(x.time, x.point, x.accuracyM))
                        if (inPlace != null && radio != null) learnFingerprint(context, inPlace.id, radio.copy(location = x.point))
                    }
                }
            }
        }
        store.saveFilter(f.state)
    }

    private fun toFix(l: Location, gnss: GnssStats?): Fix {
        val provider = when (l.provider) {
            LocationManager.GPS_PROVIDER -> Provider.GPS
            LocationManager.NETWORK_PROVIDER -> Provider.NETWORK
            LocationManager.PASSIVE_PROVIDER -> Provider.PASSIVE
            else -> Provider.FUSED
        }
        val receivedWall = System.currentTimeMillis() - (SystemClock.elapsedRealtimeNanos() - l.elapsedRealtimeNanos) / 1_000_000
        val skew = if (provider != Provider.NETWORK) l.time - receivedWall else null
        return Fix(
            time = l.time, point = LatLon(l.latitude, l.longitude), accuracyM = l.accuracy.toDouble(), provider = provider,
            isMock = l.isMock, gnssTimeSkewMs = skew,
            satellites = gnss?.satellites, cn0MeanDb = gnss?.mean, cn0StdDevDb = gnss?.std,
        )
    }

    private suspend fun motionContext(context: Context, from: Long, to: Long): MotionContext {
        val s = context.app.repo.db.signals()
        val steps = s.steps(from).filter { it.start < to }.sumOf { r ->
            val ov = (minOf(r.end, to) - maxOf(r.start, from)).coerceAtLeast(0)
            if (r.end == r.start) r.count.toDouble() else r.count * ov.toDouble() / (r.end - r.start)
        }.toInt()
        val vehicle = s.spans("vehicle", from).any { it.start < to }
        val motion = when {
            s.openSpan("vehicle") != null -> Motion.VEHICLE
            s.openSpan("bicycle") != null -> Motion.BICYCLE
            s.openSpan("running") != null -> Motion.RUNNING
            s.openSpan("walking") != null -> Motion.WALKING
            s.openSpan("still") != null -> Motion.STILL
            else -> Motion.UNKNOWN
        }
        return MotionContext(steps, motion, vehicle)
    }

    @SuppressLint("MissingPermission")
    private fun recentNetwork(context: Context, at: Long): Fix? {
        val lm = context.getSystemService(LocationManager::class.java)
        val l = runCatching { lm.getLastKnownLocation(LocationManager.NETWORK_PROVIDER) }.getOrNull() ?: return null
        if (kotlin.math.abs(l.time - at) > 5 * 60_000L) return null
        return Fix(l.time, LatLon(l.latitude, l.longitude), l.accuracy.toDouble(), Provider.NETWORK)
    }

    // ——— Стоянки и предложение места ———

    private suspend fun handleStays(context: Context, events: List<StayEvent>) {
        val repo = context.app.repo
        for (e in events) when (e) {
            is StayEvent.LongStay -> {
                val places = repo.db.places().places().map { Place(it.id, it.name, LatLon(it.lat, it.lon), it.radius) }
                val dismissed = repo.db.places().dismissed().map { LatLon(it.lat, it.lon) }
                if (!shouldSuggestPlace(e.center, places, dismissed)) continue
                val history = repo.db.places().stays(System.currentTimeMillis() - 60L * 24 * 3600_000)
                    .map { Stay(Interval(it.start, it.end), LatLon(it.lat, it.lon)) } +
                    Stay(Interval(e.start, System.currentTimeMillis()), e.center)
                val guess = guessPlaceKind(e.center, history, java.time.ZoneId.systemDefault())
                repo.settings.setPendingPlace(json.encodeToString(PendingPlace.serializer(),
                    PendingPlace(e.center.lat, e.center.lon, e.start, guess?.title)))
                context.app.day.refresh()
            }
            is StayEvent.Ended -> {
                val s = e.stay
                repo.db.places().insertStay(StayRow(start = s.interval.start, end = s.interval.end, lat = s.center.lat, lon = s.center.lon))
                repo.events.emit("stay", s.interval.start) { put("end", s.interval.end); put("lat", s.center.lat); put("lon", s.center.lon) }
            }
        }
    }

    /** Тик службы: стоянка продолжается, если стоим без шагов; без точек — узнать место по Wi-Fi и вышкам. */
    suspend fun onTick(context: Context, now: Long, stepsSinceTick: Int) = mutex.withLock {
        val s = context.app.repo.db.signals()
        val still = s.openSpan("still") != null
        handleStays(context, stays.onTick(now, stepsSinceTick, still))
        val lastFix = context.app.repo.db.places().lastGood()
        if (lastFix == null || now - lastFix.time > 30 * 60_000L) {
            val radio = radioScan(context) ?: return@withLock
            val b = book ?: LocationStore(context).loadBook().also { book = it }
            val placeId = b.match(radio)
            val writer = SignalWriter(context.app.repo)
            val places = context.app.repo.db.places().places()
            for (p in places) {
                if (p.id == placeId) writer.open("place:${p.id}", now)
            }
        }
    }

    // ——— Радиоокружение ———

    @SuppressLint("MissingPermission")
    private fun radioScan(context: Context): RadioScan? {
        if (!Permissions.granted(context, Access.LOCATION)) return null
        val wm = context.applicationContext.getSystemService(WifiManager::class.java)
        val wifi = runCatching { wm.scanResults.filter { it.level > -85 }.map { it.BSSID }.toSet() }.getOrDefault(emptySet())
        val cm = context.getSystemService(ConnectivityManager::class.java)
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        val onWifi = caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        @Suppress("DEPRECATION")
        val connected = if (onWifi) runCatching { wm.connectionInfo?.bssid }.getOrNull()?.takeIf { it != "02:00:00:00:00:00" } else null
        val hotspot = onWifi && caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) == false
        val tm = context.getSystemService(TelephonyManager::class.java)
        val cells = runCatching {
            tm.allCellInfo.orEmpty().mapNotNull { c ->
                when (c) {
                    is CellInfoLte -> c.cellIdentity.let { "lte-${it.mccString}-${it.mncString}-${it.tac}-${it.ci}" }
                    is CellInfoGsm -> c.cellIdentity.let { "gsm-${it.mccString}-${it.mncString}-${it.lac}-${it.cid}" }
                    is CellInfoWcdma -> c.cellIdentity.let { "wcdma-${it.mccString}-${it.mncString}-${it.lac}-${it.cid}" }
                    is CellInfoNr -> (c.cellIdentity as CellIdentityNr).let { "nr-${it.mccString}-${it.mncString}-${it.tac}-${it.nci}" }
                    else -> null
                }
            }.filterNot { it.contains("null") || it.contains("2147483647") }.toSet()
        }.getOrDefault(emptySet())
        if (wifi.isEmpty() && cells.isEmpty() && connected == null) return null
        return RadioScan(System.currentTimeMillis(), wifi, cells, connected, hotspot)
    }

    private suspend fun learnFingerprint(context: Context, placeId: Long, scan: RadioScan) {
        val store = LocationStore(context)
        val b = book ?: store.loadBook().also { book = it }
        b.learn(placeId, scan)
        store.saveBook(b)
        context.app.repo.db.signals().insertRadio(RadioRow(time = scan.time, wifi = scan.wifi.joinToString(","), cells = scan.cells.joinToString(","),
            connected = scan.connectedBssid, hotspot = scan.connectedIsHotspot, lat = scan.location?.lat, lon = scan.location?.lon))
    }

    /** Новое место сохранено — обучить отпечаток текущим окружением. */
    suspend fun learnHere(context: Context, placeId: Long, at: LatLon) {
        radioScan(context)?.let { learnFingerprint(context, placeId, it.copy(location = at)) }
    }
}
