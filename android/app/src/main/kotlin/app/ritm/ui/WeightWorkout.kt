package app.ritm.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import app.ritm.app
import app.ritm.data.ExerciseRow
import app.ritm.data.SetRow
import app.ritm.data.WorkoutDraft
import app.ritm.engine.Haptics
import app.ritm.engine.WorkoutClock
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch

/** Вес: одна цифра-колесо, начинается со вчерашней. */
@Composable
fun WeightScreen(host: Host, undo: UndoState) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    var kg by remember { mutableDoubleStateOf(app.day.state.value.lastWeightKg ?: 75.0) }
    var loaded by remember { mutableStateOf(app.day.state.value.lastWeightKg != null) }
    LaunchedEffect(Unit) {
        if (!loaded) { app.repo.lastWeight()?.let { kg = it.kg }; loaded = true }
    }
    Column(Modifier.fillMaxSize().navigationBarsPadding().imePadding(), horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
            WheelNumber(kg, { kg = it }, step = 0.1, unit = "кг", decimals = 1, min = 30.0, max = 250.0, style = T.huge, stepDp = 14.dp)
        }
        AccentButton("Записать", onClick = {
            scope.launch {
                val w = app.repo.addWeight(kg)
                app.day.refresh()
                undo.show("Записано") { scope.launch { app.repo.deleteWeight(w); app.day.refresh() } }
                host.done()
            }
        })
        Gap(48.dp)
    }
}

/** Тренировка: название, две цифры-колеса, подходы точками, кольцо отдыха вокруг «Подход». */
@Composable
fun WorkoutScreen(nav: Nav, host: Host) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val workout by app.repo.activeWorkout().collectAsState(initial = null)
    val rest by WorkoutClock.rest.collectAsState()
    var exercise by remember { mutableStateOf<ExerciseRow?>(null) }
    var kg by remember { mutableDoubleStateOf(20.0) }
    var reps by remember { mutableIntStateOf(10) }
    var menu by remember { mutableStateOf(false) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }

    LaunchedEffect(Unit) { if (app.repo.db.workouts().active() == null) app.repo.startWorkout(auto = false) }
    LaunchedEffect(workout?.currentExerciseId) {
        val id = workout?.currentExerciseId ?: return@LaunchedEffect
        exercise = app.repo.exercise(id)
        val draft = WorkoutDraft.get(id)
        val last = app.repo.lastSet(id)
        kg = draft?.first ?: last?.kg ?: kg
        reps = draft?.second ?: last?.reps ?: reps
    }
    LaunchedEffect(rest) { while (rest != null) { now = System.currentTimeMillis(); delay(100) } }

    val w = workout
    val sets by remember(w?.id) { if (w != null) app.repo.setsOf(w.id) else flowOf(emptyList<SetRow>()) }.collectAsState(initial = emptyList())
    val ex = exercise

    Box(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        Text("⋯", style = T.title.copy(color = C.hint), modifier = Modifier.align(Alignment.TopEnd).tap { menu = !menu }.padding(20.dp))
        if (menu) {
            Column(Modifier.align(Alignment.TopEnd).padding(top = 64.dp, end = 20.dp)) {
                Text("Завершить", style = T.body, modifier = Modifier.tap {
                    scope.launch { app.repo.endWorkout(); WorkoutClock.clear(context); app.day.refresh(); host.done() }
                }.padding(12.dp))
            }
        }
        Column(Modifier.fillMaxSize().padding(top = 72.dp, bottom = 40.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(ex?.name?.let { "$it  ▾" } ?: "Упражнение  ▾", style = T.title.copy(color = if (ex == null) C.dim else C.text),
                modifier = Modifier.tap { nav.go(Route.ExercisePick) }.padding(12.dp))
            val others = sets.map { it.exerciseId }.distinct().filter { it != ex?.id }
            if (others.isNotEmpty()) Text("ещё ${others.size} упр. сегодня", style = T.dim)
            Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
                if (ex != null) {
                    Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        WheelNumber(kg, { kg = it; WorkoutDraft.set(ex.id, it, reps) }, step = 2.5, unit = "кг", decimals = if (kg % 1.0 == 0.0) 0 else 1, max = 500.0)
                        WheelNumber(reps.toDouble(), { reps = it.toInt(); WorkoutDraft.set(ex.id, kg, it.toInt()) }, step = 1.0, unit = "повт", min = 1.0, max = 100.0)
                    }
                } else {
                    Text("Выберите упражнение", style = T.dim, modifier = Modifier.tap { nav.go(Route.ExercisePick) })
                }
            }
            Dots(sets.count { it.exerciseId == ex?.id })
            Gap(32.dp)
            val r = rest
            val progress = r?.let { ((now - it.startedAt).toFloat() / (it.endsAt - it.startedAt)).coerceIn(0f, 1f) }
            RingButton(if (r != null && progress!! < 1f) "${((r.endsAt - now) / 1000).coerceAtLeast(0)}" else "Подход", progress, onClick = {
                val e = ex ?: return@RingButton
                scope.launch {
                    WorkoutDraft.set(e.id, kg, reps)
                    if (app.repo.addSet(kg, reps) != null) {
                        Haptics.double(context)
                        WorkoutClock.startRest(context, app.repo.settings.get().restSeconds)
                        app.day.refresh()
                    }
                }
            })
        }
    }
}

/** Мои упражнения: частые сверху; новое — просто ввести название. */
@Composable
fun ExercisePickScreen(nav: Nav) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val list by app.repo.exercises().collectAsState(initial = emptyList())
    var q by remember { mutableStateOf("") }
    val fr = remember { FocusRequester() }
    fun choose(name: String) = scope.launch { app.repo.chooseExercise(name); nav.back() }
    Column(Modifier.fillMaxSize().statusBarsPadding().imePadding().padding(horizontal = 24.dp)) {
        Gap(24.dp)
        BasicTextField(
            q, { q = it }, textStyle = T.title, cursorBrush = SolidColor(C.accent), singleLine = true,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { if (q.isNotBlank()) choose(q) }),
            decorationBox = { inner -> Box { if (q.isEmpty()) Text("Название упражнения", style = T.title.copy(color = C.hint)); inner() } },
            modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp).focusRequester(fr),
        )
        LaunchedEffect(list.isEmpty()) { if (list.isEmpty()) fr.requestFocus() }
        LazyColumn {
            items(list.filter { q.isBlank() || it.name.contains(q.trim(), ignoreCase = true) }, key = { it.id }) { e ->
                Text(e.name, style = T.title, modifier = Modifier.fillMaxWidth().tap { choose(e.name) }.padding(vertical = 14.dp))
            }
        }
    }
}
