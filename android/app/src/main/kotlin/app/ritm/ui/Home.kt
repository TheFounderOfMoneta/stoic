package app.ritm.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.ui.draw.shadow
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
import androidx.compose.foundation.shape.RoundedCornerShape
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
import app.ritm.engine.Access
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
        Text("⋯", style = T.title.copy(color = C.hint), modifier = Modifier.align(Alignment.TopEnd).tap { nav.go(Route.Settings) }.padding(20.dp))

        Column(Modifier.fillMaxSize().padding(top = 64.dp, bottom = 160.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Box(Modifier.height(40.dp), contentAlignment = Alignment.Center) { NowLine(state, nav) }
            Gap(12.dp)
            val r = state.remaining
            val plan = state.plan
            DayRing(
                progress = if (plan != null && plan > 0) state.eaten.toFloat() / plan else 0f,
                over = r != null && r < 0,
                modifier = Modifier.size(248.dp).tap { nav.go(Route.FoodToday) },
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    if (r == null) {
                        Text("—", style = T.huge.copy(color = C.hint))
                        Text("ккал", style = T.dim)
                    } else {
                        FlowingNumber(abs(r))
                        Text(if (r >= 0) "ккал осталось" else "ккал сверх", style = T.dim)
                    }
                }
            }
            Gap(28.dp)
            Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
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

        BottomBar(Modifier.align(Alignment.BottomCenter), Tab.TODAY, "Добавить", onPlus = { nav.go(Route.Plus) }, onTab = { nav.toTab(it) })
        UndoBar(undo, Modifier.align(Alignment.BottomCenter).padding(bottom = 150.dp))
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
                QuietLine(if (p == Access.BUTTON) "Включить кнопку слева" else "Нет доступа: ${p.title.lowercase()}", {
                    if (p == Access.BUTTON) nav.go(Route.SettingsPage("button")) else context.startActivity(Permissions.settingsIntent(context, p))
                })
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
    Row(Modifier.fillMaxWidth().glass(16.dp).padding(horizontal = 16.dp, vertical = 13.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(20.dp).clip(CircleShape).border(1.5.dp, C.dim, CircleShape).tap(onDone))
        Spacer(Modifier.size(14.dp))
        Text(t.title, style = T.body, modifier = Modifier.weight(1f).tap(onOpen), maxLines = 2)
        t.time?.let { Text(it, style = T.dim) }
    }
}

enum class Tab { TODAY, TASKS, NOTES }

/**
 * Низ экрана: понятная кнопка действия с подписью и вкладки «Сегодня · Задачи · Заметки».
 */
@Composable
fun BottomBar(modifier: Modifier, current: Tab, plusLabel: String, onPlus: () -> Unit, onTab: (Tab) -> Unit) {
    Column(modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Row(
            Modifier
                .shadow(20.dp, RoundedCornerShape(28.dp), ambientColor = C.glow, spotColor = C.glow)
                .clip(RoundedCornerShape(28.dp))
                .background(Color.White)
                .tap(onPlus)
                .padding(start = 18.dp, end = 24.dp, top = 14.dp, bottom = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(Icons.Rounded.Add, null, tint = C.ink, modifier = Modifier.size(24.dp))
            Spacer(Modifier.size(8.dp))
            Text(plusLabel, style = T.body.copy(color = C.ink, fontWeight = androidx.compose.ui.text.font.FontWeight(600)))
        }
        Gap(12.dp)
        Row(Modifier.fillMaxWidth().glass(22.dp).padding(4.dp)) {
            listOf(Tab.TODAY to "Сегодня", Tab.TASKS to "Задачи", Tab.NOTES to "Заметки").forEach { (tab, label) ->
                val on = tab == current
                Box(
                    Modifier.weight(1f).clip(RoundedCornerShape(18.dp))
                        .background(if (on) Color.White.copy(alpha = 0.16f) else Color.Transparent)
                        .tap { if (!on) onTab(tab) }
                        .padding(vertical = 12.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(label, style = T.body.copy(color = if (on) C.text else C.dim, fontWeight = androidx.compose.ui.text.font.FontWeight(if (on) 600 else 500)))
                }
            }
        }
    }
}

/** Переходы между вкладками: «Сегодня» — главный экран, остальные — поверх него. */
fun Nav.toTab(tab: Tab) = when (tab) {
    Tab.TODAY -> home()
    Tab.TASKS -> { home(); go(Route.Tasks) }
    Tab.NOTES -> { home(); go(Route.Notes) }
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
