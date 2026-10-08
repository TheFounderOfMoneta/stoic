package app.ritm.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.ritm.app
import app.ritm.collect.PlacesActions
import app.ritm.core.day.NowItem
import app.ritm.data.TaskRow
import app.ritm.engine.DayState
import app.ritm.engine.Permissions
import kotlinx.coroutines.launch
import java.time.LocalDate
import kotlin.math.abs

/** Главный экран: одна цифра, задачи на сегодня, внизу заметки · плюс · задачи. */
@Composable
fun HomeScreen(nav: Nav, undo: UndoState) {
    val context = LocalContext.current
    val app = context.app
    val state by app.day.state.collectAsState()
    val tasks by app.repo.openTasks().collectAsState(initial = emptyList())
    val today = state.date ?: LocalDate.now()
    val todays = tasks.filter { it.date != null && !LocalDate.parse(it.date).isAfter(today) }
    val scope = rememberCoroutineScope()

    Box(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        Text("⋯", style = T.title.copy(color = C.faint), modifier = Modifier.align(Alignment.TopEnd).tap { nav.go(Route.Settings) }.padding(20.dp))

        Column(Modifier.fillMaxSize().padding(top = 120.dp, bottom = 120.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            NowLine(state, nav)
            Gap(8.dp)
            val r = state.remaining
            Column(Modifier.tap { nav.go(Route.FoodToday) }, horizontalAlignment = Alignment.CenterHorizontally) {
                if (r == null) {
                    Text("—", style = T.huge.copy(color = C.faint))
                    Text("ккал", style = T.dim)
                } else {
                    FlowingNumber(abs(r))
                    Text(if (r >= 0) "ккал" else "сверх", style = T.dim)
                }
            }
            Gap(56.dp)
            Column(Modifier.fillMaxWidth().padding(horizontal = 32.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                todays.take(5).forEach { t ->
                    TaskLine(t, onDone = {
                        scope.launch {
                            app.repo.completeTask(t)
                            undo.show("Сделано") { scope.launch { app.repo.uncompleteTask(t.copy(done = true)) } }
                        }
                    }, onOpen = { nav.go(Route.TaskEdit(t.id)) })
                }
            }
        }

        BottomBar(
            modifier = Modifier.align(Alignment.BottomCenter),
            onNotes = { nav.go(Route.Notes) },
            onPlus = { nav.go(Route.Plus) },
            onTasks = { nav.go(Route.Tasks) },
        )
        UndoBar(undo, Modifier.align(Alignment.BottomCenter).padding(bottom = 104.dp))
    }
}

@Composable
private fun NowLine(state: DayState, nav: Nav) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    AnimatedVisibility(state.nowItem != null, enter = fadeIn(), exit = fadeOut()) {
        when (state.nowItem) {
            NowItem.WORKOUT -> QuietLine("Тренировка", { nav.go(Route.Workout) })
            NowItem.PROBLEM -> state.problem?.let { p ->
                QuietLine("Нет доступа: ${p.title.lowercase()}", { context.startActivity(Permissions.settingsIntent(context, p)) })
            }
            NowItem.WEIGHT -> QuietLine("Вес сегодня?", { nav.go(Route.Weight) })
            NowItem.PLACE -> state.pendingPlace?.let { p ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(if (p.guess != null) "Похоже на «${p.guess}». Сохранить?" else "Новое место. Сохранить?", style = T.body.copy(color = C.dim))
                    Text("  Да", style = T.body.copy(color = C.accent), modifier = Modifier.tap {
                        if (p.guess != null) scope.launch { PlacesActions.savePending(context, p, p.guess) }
                        else nav.go(Route.SettingsPage("place-new"))
                    }.padding(8.dp))
                    Text("Нет", style = T.body.copy(color = C.dim), modifier = Modifier.tap { scope.launch { PlacesActions.dismissPending(context, p) } }.padding(8.dp))
                }
            }
            NowItem.SLEEP_UNSURE -> QuietLine("Спал? Проверить", { nav.go(Route.SettingsPage("sleep")) })
            null -> Spacer(Modifier.height(0.dp))
        }
    }
}

@Composable
fun TaskLine(t: TaskRow, onDone: () -> Unit, onOpen: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(22.dp).clip(CircleShape).background(C.ghost).tap(onDone))
        Spacer(Modifier.size(16.dp))
        Text(t.title, style = T.body, modifier = Modifier.weight(1f).tap(onOpen), maxLines = 2)
        t.time?.let { Text(it, style = T.dim) }
    }
}

@Composable
fun BottomBar(modifier: Modifier, onNotes: () -> Unit, onPlus: () -> Unit, onTasks: () -> Unit) {
    Row(
        modifier.fillMaxWidth().padding(horizontal = 40.dp, vertical = 20.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Outlined.Edit, "Заметки", tint = C.dim, modifier = Modifier.size(26.dp).tap(onNotes))
        Box(Modifier.size(64.dp).clip(CircleShape).background(C.accent).tap(onPlus), contentAlignment = Alignment.Center) {
            Icon(Icons.Rounded.Add, "Добавить", tint = Color.Black, modifier = Modifier.size(32.dp))
        }
        Icon(Icons.Outlined.CheckCircle, "Задачи", tint = C.dim, modifier = Modifier.size(26.dp).tap(onTasks))
    }
}

/** «+»: пять слов, без иконок. */
@Composable
fun PlusScreen(nav: Nav, host: Host) {
    Column(
        Modifier.fillMaxSize().navigationBarsPadding().padding(bottom = 48.dp, start = 40.dp),
        verticalArrangement = Arrangement.Bottom,
    ) {
        listOf(
            "Еда" to Route.FoodPick,
            "Заметка" to Route.NoteEdit(),
            "Задача" to Route.TaskEdit(),
            "Вес" to Route.Weight,
            "Тренировка" to Route.Workout,
        ).forEach { (label, route) ->
            Text(label, style = T.title.copy(fontSize = T.title.fontSize * 1.3f), modifier = Modifier.fillMaxWidth().tap { nav.replace(route) }.padding(vertical = 16.dp))
        }
        Gap(24.dp)
        Text("Закрыть", style = T.dim, modifier = Modifier.tap { if (!nav.back()) host.done() }.padding(vertical = 8.dp))
    }
}
