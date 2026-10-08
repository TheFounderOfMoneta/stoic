package app.ritm.ui

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.lifecycle.lifecycleScope
import app.ritm.app
import app.ritm.collect.CollectorService
import app.ritm.engine.DemoSeed
import kotlinx.coroutines.launch

/** Экраны по маршруту. Общие для приложения и быстрого ввода. */
@Composable
fun Screens(nav: Nav, host: Host, undo: UndoState) {
    BackHandler { if (!nav.back()) host.done() }
    AmbientBackground()
    // Состояние экранов в стеке сохраняется: ушёл вглубь и вернулся — введённое на месте.
    val holder = androidx.compose.runtime.saveable.rememberSaveableStateHolder()
    AnimatedContent(nav.top, transitionSpec = { fadeIn(tween(260)) togetherWith fadeOut(tween(160)) }, label = "screen") { r ->
        androidx.compose.runtime.CompositionLocalProvider(LocalNav provides nav, LocalRoute provides r) {
        holder.SaveableStateProvider(System.identityHashCode(r)) {
        Box(Modifier.fillMaxSize()) {
            when (r) {
                Route.Home -> HomeScreen(nav, undo)
                Route.Tasks -> if (host.quick) Unlock(host, r) else TasksScreen(nav, undo)
                Route.Notes -> if (host.quick) Unlock(host, r) else NotesScreen(nav, undo)
                Route.Settings -> if (host.quick) Unlock(host, r) else SettingsScreen(nav)
                Route.FoodToday -> if (host.quick) Unlock(host, r) else FoodTodayScreen(nav, undo)
                is Route.SettingsPage -> if (host.quick) Unlock(host, r) else SettingsPageScreen(nav, r.page)
                Route.Plus -> PlusScreen(nav, host)
                Route.FoodPick -> FoodPickScreen(nav, host, undo)
                is Route.FoodAmount -> FoodAmountScreen(nav, host, undo, r.productId, r.editFoodId)
                is Route.AddProduct -> AddProductScreen(nav, r.name)
                is Route.EditProduct -> EditProductScreen(nav, r.productId)
                Route.Weight -> WeightScreen(host, undo)
                Route.Workout -> WorkoutScreen(nav, host)
                Route.ExercisePick -> ExercisePickScreen(nav)
                is Route.TaskEdit -> if (host.quick && r.id != null) Unlock(host, r) else TaskEditScreen(nav, host, r.id)
                is Route.NoteEdit -> if (host.quick && r.id != null) Unlock(host, r) else NoteEditScreen(r.id)
                Route.Onboarding -> OnboardingScreen { nav.stack.clear(); nav.stack.add(Route.Home) }
            }
        }
        }
        }
    }
}

/** Смотреть и править — только после разблокировки: системный отпечаток, потом сразу нужный экран. */
@Composable
private fun Unlock(host: Host, route: Route) {
    LaunchedEffect(route) { host.needsUnlock(route) }
}

class MainActivity : ComponentActivity() {
    private val nav = Nav(Route.Home)
    private val undo = UndoState()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(android.graphics.Color.TRANSPARENT), SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        val host = object : Host {
            override val quick = false
            // «Готово» — назад туда, откуда пришёл (записал еду — снова список еды), а не на главную.
            override fun done() { if (!nav.back()) finish() }
            override fun needsUnlock(route: Route) {}
        }
        intent?.getStringExtra(EXTRA_ROUTE)?.let { routeOf(it)?.let(nav::go) }
        intent?.getStringExtra(EXTRA_DEMO)?.let { kind -> lifecycleScope.launch { DemoSeed.run(this@MainActivity, kind) } }
        setContent {
            RitmTheme {
                val prefs by app.repo.settings.flow.collectAsState(initial = null)
                val p = prefs ?: return@RitmTheme
                LaunchedEffect(p.onboarded) {
                    if (!p.onboarded && nav.top != Route.Onboarding) { nav.stack.clear(); nav.stack.add(Route.Onboarding) }
                }
                Screens(nav, host, undo)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(EXTRA_ROUTE)?.let { routeOf(it)?.let(nav::go) }
        intent.getStringExtra(EXTRA_DEMO)?.let { kind -> lifecycleScope.launch { DemoSeed.run(this@MainActivity, kind) } }
    }

    override fun onResume() {
        super.onResume()
        lifecycleScope.launch {
            if (app.repo.settings.get().onboarded) CollectorService.start(this@MainActivity)
            app.day.refresh()
        }
    }

    companion object {
        const val EXTRA_ROUTE = "route"
        const val EXTRA_DEMO = "demo"
    }
}

/** Быстрый ввод поверх экрана блокировки: только формы ввода. */
class QuickActivity : ComponentActivity() {
    private lateinit var nav: Nav
    private val undo = UndoState()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(android.graphics.Color.TRANSPARENT), SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        nav = Nav(routeOf(intent?.getStringExtra(EXTRA_SCREEN) ?: PLUS) ?: Route.Plus)
        val host = object : Host {
            override val quick = true
            override fun done() { if (!nav.back()) finish() }
            override fun needsUnlock(route: Route) {
                val km = getSystemService(KeyguardManager::class.java)
                val open = {
                    startActivity(Intent(this@QuickActivity, MainActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                        .putExtra(MainActivity.EXTRA_ROUTE, keyOf(route)))
                    finish()
                }
                if (!km.isKeyguardLocked) { open(); return }
                km.requestDismissKeyguard(this@QuickActivity, object : KeyguardManager.KeyguardDismissCallback() {
                    override fun onDismissSucceeded() { open() }
                    override fun onDismissCancelled() { if (!nav.back()) finish() }
                    override fun onDismissError() { if (!nav.back()) finish() }
                })
            }
        }
        setContent { RitmTheme { Screens(nav, host, undo) } }
    }

    companion object {
        const val EXTRA_SCREEN = "screen"
        const val PLUS = "plus"
        const val FOOD = "food"
        const val WEIGHT = "weight"
        const val WORKOUT = "workout"
        const val TASK = "task"
        const val NOTE = "note"

        fun intent(context: Context, screen: String): Intent =
            Intent(context, QuickActivity::class.java).putExtra(EXTRA_SCREEN, screen)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
    }
}

private fun routeOf(key: String): Route? = when (key) {
    QuickActivity.PLUS -> Route.Plus
    QuickActivity.FOOD -> Route.FoodPick
    QuickActivity.WEIGHT -> Route.Weight
    QuickActivity.WORKOUT -> Route.Workout
    QuickActivity.TASK -> Route.TaskEdit()
    QuickActivity.NOTE -> Route.NoteEdit()
    "tasks" -> Route.Tasks
    "notes" -> Route.Notes
    "settings" -> Route.Settings
    "food-today" -> Route.FoodToday
    else -> key.removePrefix("page:").takeIf { key.startsWith("page:") }?.let { Route.SettingsPage(it) }
}

private fun keyOf(r: Route): String = when (r) {
    Route.Tasks -> "tasks"
    Route.Notes -> "notes"
    Route.Settings -> "settings"
    Route.FoodToday -> "food-today"
    is Route.SettingsPage -> "page:${r.page}"
    is Route.TaskEdit -> "tasks"
    is Route.NoteEdit -> "notes"
    else -> "plus"
}
