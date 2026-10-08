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
    var initial by remember { mutableStateOf<Double?>(if (loaded) kg else null) }
    var done by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        if (!loaded) { app.repo.lastWeight()?.let { kg = it.kg }; loaded = true; initial = kg }
    }
    // Поменял вес и вышел — записать.
    OnLeave {
        val start = initial ?: return@OnLeave
        if (done || kotlin.math.abs(kg - start) < 0.05) return@OnLeave
        app.repo.addWeight(kg)
        app.day.refresh()
    }
    Column(Modifier.fillMaxSize().navigationBarsPadding().imePadding(), horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
            WheelNumber(kg, { kg = it }, step = 0.1, unit = "кг", decimals = 1, min = 30.0, max = 250.0, style = T.huge, stepDp = 14.dp)
        }
        AccentButton("Записать", onClick = {
            done = true
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

    var clock by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { clock = System.currentTimeMillis(); delay(1_000) } }
    val setsHere = sets.count { it.exerciseId == ex?.id }

    Box(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        Column(Modifier.fillMaxSize().padding(top = 20.dp, bottom = 32.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            // Верх: сколько идёт тренировка и «Завершить» — на виду.
            Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp), verticalAlignment = Alignment.CenterVertically) {
                val minutes = w?.let { ((clock - it.start) / 60_000).coerceAtLeast(0) } ?: 0
                Text("Тренировка · $minutes мин", style = T.dim, modifier = Modifier.weight(1f))
                Text("Завершить", style = T.body.copy(color = C.accent), modifier = Modifier.tap {
                    scope.launch { app.repo.endWorkout(); WorkoutClock.clear(context); app.day.refresh(); host.done() }
                }.padding(vertical = 8.dp))
            }
            if (ex == null) {
                Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("Что делаете?", style = T.title)
                        Gap(20.dp)
                        AccentButton("Выбрать упражнение", onClick = { nav.go(Route.ExercisePick) })
                    }
                }
            } else {
                Gap(28.dp)
                Text(ex.name, style = T.title, modifier = Modifier.padding(horizontal = 20.dp))
                Text("сменить упражнение", style = T.dim.copy(color = C.accent), modifier = Modifier.tap { nav.go(Route.ExercisePick) }.padding(8.dp))
                val others = sets.map { it.exerciseId }.distinct().filter { it != ex.id }
                if (others.isNotEmpty()) Text("ещё ${others.size} упр. сегодня", style = T.dim)
                Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                            WheelNumber(kg, { kg = it; WorkoutDraft.set(ex.id, it, reps) }, step = 2.5, unit = "кг", decimals = if (kg % 1.0 == 0.0) 0 else 1, max = 500.0)
                            WheelNumber(reps.toDouble(), { reps = it.toInt(); WorkoutDraft.set(ex.id, kg, it.toInt()) }, step = 1.0, unit = "повторов", min = 1.0, max = 100.0)
                        }
                        Text("крутите пальцем вверх-вниз или нажмите на цифру", style = T.dim.copy(color = C.hint))
                    }
                }
                Dots(setsHere)
                if (setsHere > 0) Text("подходов: $setsHere", style = T.dim, modifier = Modifier.padding(top = 6.dp))
                Gap(20.dp)
                val r = rest
                val progress = r?.let { ((now - it.startedAt).toFloat() / (it.endsAt - it.startedAt)).coerceIn(0f, 1f) }
                val resting = r != null && progress!! < 1f
                RingButton(if (resting) "${((r!!.endsAt - now) / 1000).coerceAtLeast(0)}" else "Подход", progress, onClick = {
                    scope.launch {
                        WorkoutDraft.set(ex.id, kg, reps)
                        if (app.repo.addSet(kg, reps) != null) {
                            Haptics.double(context)
                            WorkoutClock.startRest(context, app.repo.settings.get().restSeconds)
                            app.day.refresh()
                        }
                    }
                })
                Text(
                    when {
                        resting -> "отдых · можно записать подход раньше"
                        setsHere == 0 -> "сделали подход — нажмите"
                        else -> "следующий подход — нажмите"
                    },
                    style = T.dim, modifier = Modifier.padding(top = 10.dp),
                )
            }
        }
    }
}

/**
 * Выбор упражнения: готовый список — просто нажать. Своё — начать печатать, сверху появится
 * кнопка «Добавить «…»». Частые поднимаются наверх.
 */
@Composable
fun ExercisePickScreen(nav: Nav) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val list by app.repo.exercises().collectAsState(initial = emptyList())
    var q by remember { mutableStateOf("") }
    fun choose(name: String) = scope.launch { app.repo.chooseExercise(name); nav.back() }
    val query = q.trim()
    val shown = list.filter { query.isEmpty() || it.name.contains(query, ignoreCase = true) }
    val exact = list.any { it.name.equals(query, ignoreCase = true) }
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding().padding(horizontal = 20.dp)) {
        Gap(24.dp)
        Text("Упражнение", style = T.title)
        Gap(16.dp)
        BasicTextField(
            q, { q = it }, textStyle = T.body, cursorBrush = SolidColor(C.accent), singleLine = true,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { if (query.isNotEmpty()) choose(shown.firstOrNull { it.name.equals(query, true) }?.name ?: query) }),
            decorationBox = { inner -> Box { if (q.isEmpty()) Text("Найти или ввести своё", style = T.body.copy(color = C.hint)); inner() } },
            modifier = Modifier.fillMaxWidth().glass(16.dp).padding(horizontal = 16.dp, vertical = 14.dp),
        )
        if (query.isNotEmpty() && !exact) {
            Gap(14.dp)
            AccentButton("+ Добавить «$query»", onClick = { choose(query) }, modifier = Modifier.fillMaxWidth())
        }
        Gap(12.dp)
        LazyColumn(Modifier.weight(1f)) {
            val used = shown.filter { it.uses > 0 }
            val rest = shown.filter { it.uses == 0 }
            if (used.isNotEmpty()) {
                item(key = "h-used") { Text("Частые", style = T.dim, modifier = Modifier.padding(top = 8.dp, bottom = 4.dp)) }
                items(used, key = { it.id }) { e -> ExerciseLine(e.name) { choose(e.name) } }
            }
            if (rest.isNotEmpty()) {
                item(key = "h-all") { Text(if (used.isEmpty()) "Выберите — или введите своё сверху" else "Все", style = T.dim, modifier = Modifier.padding(top = 12.dp, bottom = 4.dp)) }
                items(rest, key = { it.id }) { e -> ExerciseLine(e.name) { choose(e.name) } }
            }
        }
    }
}

@Composable
private fun ExerciseLine(name: String, onClick: () -> Unit) {
    Text(name, style = T.row, modifier = Modifier.fillMaxWidth().tap(onClick).padding(vertical = 13.dp))
}
