package app.ritm

import android.app.Application
import android.content.Context
import app.ritm.data.Repo
import app.ritm.data.RitmDb
import app.ritm.data.Settings
import app.ritm.engine.DayModel
import app.ritm.engine.Notifications
import app.ritm.work.Work
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class RitmApp : Application() {
    lateinit var repo: Repo
        private set
    lateinit var day: DayModel
        private set
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        val db = RitmDb.create(this)
        repo = Repo(this, db, Settings(this))
        day = DayModel(this, repo)
        Notifications.channels(this)
        scope.launch {
            repo.settings.ensureInstalledAt(System.currentTimeMillis())
            repo.seedProducts()
            day.refresh()
        }
        Work.schedule(this)
    }
}

val Context.app: RitmApp get() = applicationContext as RitmApp
