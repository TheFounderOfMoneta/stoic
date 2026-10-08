package app.ritm.ui

import android.media.MediaPlayer
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
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
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
import app.ritm.data.NoteRow
import app.ritm.data.TaskRow
import app.ritm.engine.Notifications
import app.ritm.work.TaskAlarms
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Locale

private val dayMonth = DateTimeFormatter.ofPattern("d MMMM", Locale("ru"))

/** Задачи: Сегодня / Завтра / Потом. Свайп вправо — сделано, влево — удалить. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(nav: Nav, undo: UndoState) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val tasks by app.repo.openTasks().collectAsState(initial = emptyList())
    val today = app.day.state.collectAsState().value.date ?: LocalDate.now()
    val groups = listOf(
        "Сегодня" to tasks.filter { it.date != null && !LocalDate.parse(it.date).isAfter(today) },
        "Завтра" to tasks.filter { it.date != null && LocalDate.parse(it.date) == today.plusDays(1) },
        "Потом" to tasks.filter { it.date == null || LocalDate.parse(it.date).isAfter(today.plusDays(1)) },
    ).filter { it.second.isNotEmpty() }

    Box(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 32.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(top = 48.dp, bottom = 140.dp)) {
            if (groups.isEmpty()) item { Text("Задач нет. Плюс внизу — добавить", style = T.dim) }
            groups.forEach { (title, list) ->
                item(key = "h$title") { Text(title, style = T.dim, modifier = Modifier.padding(top = 24.dp, bottom = 4.dp)) }
                items(list, key = { it.id }) { t ->
                    val state = rememberSwipeToDismissBoxState(confirmValueChange = { v ->
                        when (v) {
                            SwipeToDismissBoxValue.StartToEnd -> {
                                scope.launch { app.repo.completeTask(t); TaskAlarms.cancel(context, t.id) }
                                undo.show("Сделано") { scope.launch { app.repo.uncompleteTask(t) } }
                                true
                            }
                            SwipeToDismissBoxValue.EndToStart -> {
                                scope.launch { app.repo.deleteTask(t); TaskAlarms.cancel(context, t.id) }
                                undo.show("Удалено") { scope.launch { app.repo.restoreTask(t) } }
                                true
                            }
                            else -> false
                        }
                    })
                    SwipeToDismissBox(state, backgroundContent = {}) {
                        Row(Modifier.fillMaxWidth().tap { nav.go(Route.TaskEdit(t.id)) }.padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(t.title, style = T.body, modifier = Modifier.weight(1f))
                            val d = t.date?.let(LocalDate::parse)
                            val label = listOfNotNull(d?.takeIf { it.isAfter(today.plusDays(1)) }?.format(dayMonth), t.time).joinToString(" ")
                            if (label.isNotEmpty()) Text(label, style = T.dim)
                        }
                    }
                }
            }
        }
        BottomBar(Modifier.align(Alignment.BottomCenter), onNotes = { nav.replace(Route.Notes) }, onPlus = { nav.go(Route.TaskEdit()) }, onTasks = { nav.back() })
        UndoBar(undo, Modifier.align(Alignment.BottomCenter).padding(bottom = 104.dp))
    }
}

/** Ввод задачи: строка и чипы. После ввода поле очищается — можно надиктовать список подряд. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskEditScreen(nav: Nav, host: Host, id: Long?) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    var existing by remember { mutableStateOf<TaskRow?>(null) }
    var title by remember { mutableStateOf("") }
    var date by remember { mutableStateOf<LocalDate?>(null) }
    var time by remember { mutableStateOf<String?>(null) }
    var pickDate by remember { mutableStateOf(false) }
    var pickTime by remember { mutableStateOf(false) }
    var added by remember { mutableStateOf(0) }
    val fr = remember { FocusRequester() }
    val today = app.day.state.value.date ?: LocalDate.now()

    LaunchedEffect(id) {
        if (id != null) app.repo.task(id)?.let { t -> existing = t; title = t.title; date = t.date?.let(LocalDate::parse); time = t.time }
        fr.requestFocus()
    }

    fun commit() {
        val text = title.trim()
        if (text.isEmpty()) return
        val e = existing
        scope.launch {
            val saved = if (e != null) {
                e.copy(title = text, date = date?.toString(), time = time).also { app.repo.updateTask(it) }
            } else app.repo.addTask(text, date, time)
            TaskAlarms.cancel(context, saved.id)
            Notifications.cancelTask(context, saved.id)
            TaskAlarms.timeOf(saved.date, saved.time)?.let { TaskAlarms.schedule(context, saved.id, it) }
            app.day.refresh()
            if (e != null) nav.back() else { title = ""; added++ }
        }
    }

    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding().padding(horizontal = 24.dp)) {
        Gap(48.dp)
        BasicTextField(
            title, { title = it }, textStyle = T.title, cursorBrush = SolidColor(C.accent),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { commit() }),
            decorationBox = { inner -> Box { if (title.isEmpty()) Text("Что сделать?", style = T.title.copy(color = C.faint)); inner() } },
            modifier = Modifier.fillMaxWidth().focusRequester(fr),
        )
        Gap(24.dp)
        val chosen = when (date) { null -> "later"; today -> "today"; today.plusDays(1) -> "tomorrow"; else -> "date" }
        Chips(
            listOf("later" to "Потом", "today" to "Сегодня", "tomorrow" to "Завтра", "date" to (date?.takeIf { chosen == "date" }?.format(dayMonth) ?: "Дата…")),
            chosen,
            { v -> when (v) { "later" -> { date = null; time = null }; "today" -> date = today; "tomorrow" -> date = today.plusDays(1); else -> pickDate = true } },
        )
        Gap(8.dp)
        Text(time?.let { "в $it  ×" } ?: "Время…", style = T.body.copy(color = if (time != null) C.accent else C.dim),
            modifier = Modifier.tap { if (time != null) time = null else pickTime = true }.padding(vertical = 8.dp, horizontal = 14.dp))
        if (added > 0) Text("Добавлено: $added", style = T.dim, modifier = Modifier.padding(top = 16.dp))
        Box(Modifier.weight(1f))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
            AccentButton(if (existing != null) "Сохранить" else if (title.isBlank() && added > 0) "Готово" else "Добавить", onClick = {
                if (title.isBlank() && added > 0) host.done() else commit()
            })
        }
        Gap(32.dp)
    }

    if (pickDate) {
        val st = rememberDatePickerState(initialSelectedDateMillis = (date ?: today).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli())
        DatePickerDialog(onDismissRequest = { pickDate = false }, confirmButton = {
            TextButton({ st.selectedDateMillis?.let { date = Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate() }; pickDate = false }) { Text("Готово") }
        }) { DatePicker(st) }
    }
    if (pickTime) {
        val now = LocalTime.now()
        val st = rememberTimePickerState(initialHour = now.hour + 1, initialMinute = 0, is24Hour = true)
        DatePickerDialog(onDismissRequest = { pickTime = false }, confirmButton = {
            TextButton({ time = String.format(Locale.US, "%02d:%02d", st.hour, st.minute); if (date == null) date = today; pickTime = false }) { Text("Готово") }
        }) { Box(Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) { TimePicker(st) } }
    }
}

private val timeFmt = DateTimeFormatter.ofPattern("HH:mm")
private val dateTimeFmt = DateTimeFormatter.ofPattern("d MMM, HH:mm", Locale("ru"))

/** Заметки: тексты, новые сверху. Поиск — первая строка списка (потянуть вниз). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NotesScreen(nav: Nav, undo: UndoState) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val notes by app.repo.notes().collectAsState(initial = emptyList())
    var q by remember { mutableStateOf("") }
    val shown = notes.filter { q.isBlank() || it.text.contains(q.trim(), ignoreCase = true) }
    Box(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 32.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(top = 16.dp, bottom = 140.dp)) {
            item {
                BasicTextField(q, { q = it }, textStyle = T.body, cursorBrush = SolidColor(C.accent), singleLine = true,
                    decorationBox = { inner -> Box { if (q.isEmpty()) Text("Поиск", style = T.body.copy(color = C.faint)); inner() } },
                    modifier = Modifier.fillMaxWidth().padding(vertical = 16.dp))
            }
            if (notes.isEmpty()) item { Text("Удерживайте кнопку слева и говорите — заметка появится здесь", style = T.dim) }
            items(shown, key = { it.id }) { n ->
                val state = rememberSwipeToDismissBoxState(confirmValueChange = { v ->
                    if (v == SwipeToDismissBoxValue.EndToStart) {
                        scope.launch { app.repo.deleteNote(n) }
                        undo.show("Удалено") { scope.launch { app.repo.restoreNote(n) } }
                        true
                    } else false
                })
                SwipeToDismissBox(state, backgroundContent = {}, enableDismissFromStartToEnd = false) {
                    NoteCard(n) { nav.go(Route.NoteEdit(n.id)) }
                }
            }
        }
        BottomBar(Modifier.align(Alignment.BottomCenter), onNotes = { nav.back() }, onPlus = { nav.go(Route.NoteEdit()) }, onTasks = { nav.replace(Route.Tasks) })
        UndoBar(undo, Modifier.align(Alignment.BottomCenter).padding(bottom = 104.dp))
    }
}

@Composable
private fun NoteCard(n: NoteRow, onClick: () -> Unit) {
    val at = Instant.ofEpochMilli(n.at).atZone(ZoneId.systemDefault())
    Column(Modifier.fillMaxWidth().tap(onClick).padding(vertical = 14.dp)) {
        val head = when (n.kind) {
            "moment" -> "⚑ ${at.format(dateTimeFmt)}"
            "voice" -> "▶ ${(n.audioMs ?: 0) / 1000 / 60}:${String.format(Locale.US, "%02d", (n.audioMs ?: 0) / 1000 % 60)}"
            else -> null
        }
        if (head != null) Text(head, style = T.body.copy(color = C.accent))
        if (n.text.isNotBlank()) Text(n.text, style = T.body, maxLines = 2)
        if (n.kind != "moment") Text(at.format(dateTimeFmt), style = T.dim)
    }
}

/** Заметка: весь экран — текст. Сохраняется на ходу, «назад» — готово. */
@Composable
fun NoteEditScreen(id: Long?) {
    val context = LocalContext.current
    val app = context.app
    var note by remember { mutableStateOf<NoteRow?>(null) }
    var text by remember { mutableStateOf("") }
    var playing by remember { mutableStateOf<MediaPlayer?>(null) }
    val fr = remember { FocusRequester() }

    LaunchedEffect(id) {
        val n = if (id != null) app.repo.note(id) else app.repo.addNote("")
        note = n; text = n?.text ?: ""
        if (n?.kind != "voice") fr.requestFocus()
    }
    LaunchedEffect(text) {
        delay(400)
        note?.let { app.repo.saveNoteText(it, text) }
    }
    DisposableEffect(Unit) {
        onDispose {
            playing?.release()
            val n = note
            if (n != null) app.scope.launch { app.repo.saveNoteText(n, text); app.repo.publishNote(n.id) }
        }
    }
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding().padding(horizontal = 24.dp)) {
        Gap(32.dp)
        note?.audioPath?.let { path ->
            Text(if (playing != null) "■ Стоп" else "▶ Прослушать", style = T.body.copy(color = C.accent), modifier = Modifier.tap {
                val p = playing
                if (p != null) { p.release(); playing = null } else {
                    playing = MediaPlayer().apply { setDataSource(path); setOnCompletionListener { it.release(); playing = null }; prepare(); start() }
                }
            }.padding(vertical = 12.dp))
        }
        BasicTextField(
            text, { text = it }, textStyle = T.body.copy(lineHeight = T.body.fontSize * 1.4f), cursorBrush = SolidColor(C.accent),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            decorationBox = { inner -> Box { if (text.isEmpty()) Text("Мысль…", style = T.body.copy(color = C.faint)); inner() } },
            modifier = Modifier.fillMaxSize().focusRequester(fr),
        )
    }
}
