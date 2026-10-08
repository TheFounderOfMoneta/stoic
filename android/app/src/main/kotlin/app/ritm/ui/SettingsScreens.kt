package app.ritm.ui

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import app.ritm.BuildConfigProxy
import app.ritm.app
import app.ritm.button.ButtonBrain
import app.ritm.button.ButtonService
import app.ritm.button.LogcatKeySource
import app.ritm.button.READ_LOGS_COMMAND
import app.ritm.button.canReadLogs
import app.ritm.collect.CollectorService
import app.ritm.backup.Backup
import app.ritm.voice.Transcriber
import app.ritm.collect.LocationCollector
import app.ritm.collect.PlacesActions
import app.ritm.core.energy.Body
import app.ritm.core.energy.Energy
import androidx.compose.runtime.saveable.rememberSaveable
import kotlin.math.roundToInt
import app.ritm.core.energy.GoalType
import app.ritm.core.energy.Sex
import app.ritm.core.energy.WeightGoal
import app.ritm.core.energy.dailyPlan
import app.ritm.data.PlaceRow
import app.ritm.engine.Access
import app.ritm.engine.Permissions
import app.ritm.engine.formatInt
import app.ritm.work.Work
import kotlinx.coroutines.launch
import kotlinx.serialization.json.put
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@Composable
private fun Page(title: String?, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp)) {
        Gap(40.dp)
        if (title != null) { Text(title, style = T.title); Gap(24.dp) }
        content()
        Gap(48.dp)
    }
}

@Composable
private fun Item(title: String, why: String, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = 5.dp).glass(18.dp).tap(onClick).padding(horizontal = 18.dp, vertical = 14.dp)) {
        Text(title, style = T.body)
        Text(why, style = T.dim)
    }
}

/** Сверху 5 главных, у каждой одна строка «зачем». Остальное — в «Дополнительно». */
@Composable
fun SettingsScreen(nav: Nav) {
    val prefs by LocalContext.current.app.repo.settings.flow.collectAsState(initial = null)
    Page(null) {
        Item("Цели", "На что ориентируются ИИ и сводки") { nav.go(Route.SettingsPage("goals")) }
        Item("Тело", "Пол, рост, возраст, вес — отсюда базовый расход") { nav.go(Route.SettingsPage("body")) }
        Item("Мои места", "Дом, работа, зал — по ним сон и тренировки") { nav.go(Route.SettingsPage("places")) }
        Item("Кнопка слева", "Удержание — голос, 1 — главное, 2 — еда, 3 — момент") { nav.go(Route.SettingsPage("button")) }
        val sync = prefs?.lastSyncAt?.takeIf { it > 0 }?.let { "отправлено ${ago(it)}" } ?: "сервер не задан"
        Item("Сервер", "Куда уходят данные для ИИ · $sync") { nav.go(Route.SettingsPage("server")) }
        Gap(16.dp)
        Item("Дополнительно", "Доступы, отдых, экран блокировки, версия") { nav.go(Route.SettingsPage("advanced")) }
    }
}

private fun ago(t: Long): String {
    val m = (System.currentTimeMillis() - t) / 60_000
    return when { m < 1 -> "только что"; m < 60 -> "$m мин назад"; m < 24 * 60 -> "${m / 60} ч назад"; else -> "${m / 1440} дн назад" }
}

@Composable
fun SettingsPageScreen(nav: Nav, page: String) {
    when (page) {
        "goals" -> GoalsPage(nav)
        "body" -> BodyPage(nav)
        "places" -> PlacesPage()
        "place-new" -> NewPlacePage(nav)
        "button" -> ButtonPage()
        "server" -> ServerPage(nav)
        "advanced" -> AdvancedPage(nav)
        "sleep" -> SleepPage(nav)
        "diag" -> DiagPage()
    }
}

@Composable
fun GoalsPage(nav: Nav?, onNext: (() -> Unit)? = null) {
    val app = LocalContext.current.app
    val scope = rememberCoroutineScope()
    var text by rememberSaveable { mutableStateOf("") }
    var saved by rememberSaveable { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) { if (saved == null) { text = app.repo.settings.get().goals; saved = text } }
    suspend fun save() {
        val t = text.trim()
        if (saved == null || t == saved) return
        app.repo.settings.setGoals(t)
        app.repo.events.emit("profile.goals") { put("text", t) }
        saved = t
    }
    OnLeave { save() }
    Page("Чего хотите достичь?") {
        BasicTextField(text, { text = it }, textStyle = T.body, cursorBrush = SolidColor(C.accent),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            decorationBox = { inner -> Box { if (text.isEmpty()) Text("Своими словами. Например: похудеть до 80 кг, выучить английский", style = T.body.copy(color = C.hint)); inner() } },
            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        Gap(32.dp)
        AccentButton(if (onNext != null) "Дальше" else "Готово", onClick = {
            scope.launch {
                save()
                onNext?.invoke() ?: nav?.back()
            }
        })
        if (onNext != null) Text("Пропустить", style = T.dim, modifier = Modifier.tap { onNext() }.padding(vertical = 16.dp))
    }
}


/**
 * Пол, рост, год рождения, вес — из них считается только базовый расход (Миффлин — Сан Жеор).
 * Цели по весу здесь нет: по этим данным её не определить.
 */
@Composable
fun BodyPage(nav: Nav?, onNext: (() -> Unit)? = null) {
    val app = LocalContext.current.app
    val scope = rememberCoroutineScope()
    var sex by rememberSaveable { mutableStateOf<Sex?>(null) }
    var height by rememberSaveable { mutableDoubleStateOf(175.0) }
    var birthYear by rememberSaveable { mutableIntStateOf(1995) }
    var weight by rememberSaveable { mutableDoubleStateOf(75.0) }
    var loaded by rememberSaveable { mutableStateOf(false) }
    var savedWeight by rememberSaveable { mutableStateOf<Double?>(null) }
    LaunchedEffect(Unit) {
        if (loaded) return@LaunchedEffect
        val p = app.repo.settings.get()
        p.sex?.let { sex = it }; p.heightCm?.let { height = it }; p.birthDate?.let { birthYear = it.year }
        app.repo.lastWeight()?.let { weight = it.kg; savedWeight = it.kg }
        loaded = true
    }
    val birth = LocalDate.of(birthYear, 7, 1)
    var savedBody by rememberSaveable { mutableStateOf("") }
    suspend fun save() {
        val s = sex ?: return
        if (!loaded) return
        val key = "$s|$height|$birthYear|$weight"
        if (key == savedBody) return
        app.repo.settings.setBody(s, height, birth)
        app.repo.settings.setGoal(GoalType.KEEP, 0.0)
        if (savedWeight == null || kotlin.math.abs(savedWeight!! - weight) > 0.05) { app.repo.addWeight(weight); savedWeight = weight }
        app.repo.events.emit("profile.body") { put("sex", s.name); put("heightCm", height); put("birthYear", birthYear) }
        savedBody = key
        app.day.refresh()
    }
    // Всё введённое сохраняется само при выходе с экрана.
    OnLeave { save() }
    val bmr = sex?.let { Energy.bmrPerDay(Body(it, height, birth), weight, LocalDate.now()).roundToInt() }
    Page(if (onNext != null) "О себе" else "Тело") {
        Chips(listOf(Sex.MALE to "Мужчина", Sex.FEMALE to "Женщина"), sex, { sex = it })
        Gap(28.dp)
        BodyRow("Рост") { WheelNumber(height, { height = it }, 1.0, "см", style = T.title, min = 120.0, max = 230.0) }
        BodyRow("Год рождения") { WheelNumber(birthYear.toDouble(), { birthYear = it.toInt() }, 1.0, "", style = T.title, min = 1930.0, max = LocalDate.now().year - 10.0) }
        BodyRow("Вес") { WheelNumber(weight, { weight = it }, 0.1, "кг", decimals = 1, style = T.title, min = 30.0, max = 250.0, stepDp = 14.dp) }
        Gap(28.dp)
        if (bmr != null) {
            Text("Базовый расход", style = T.dim)
            Text("≈ ${formatInt(bmr)} ккал в день", style = T.title.copy(color = C.accent))
            Text("Столько тело тратит в покое. Шаги и тренировки Ритм добавит сам по данным.", style = T.dim)
        } else {
            Text("Выберите пол — посчитаю базовый расход", style = T.dim)
        }
        Gap(32.dp)
        AccentButton(if (onNext != null) "Дальше" else "Готово", enabled = sex != null, onClick = {
            scope.launch { save(); onNext?.invoke() ?: nav?.back() }
        })
    }
}

/** Строка «подпись — цифра»: понятно, что крутить и куда нажимать. */
@Composable
private fun BodyRow(label: String, content: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp).glass(18.dp).padding(start = 18.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = T.body, modifier = Modifier.weight(1f))
        content()
    }
}

@Composable
private fun PlacesPage() {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val places by app.repo.db.places().placesFlow().collectAsState(initial = emptyList())
    var editing by remember { mutableStateOf<PlaceRow?>(null) }
    Page("Мои места") {
        if (places.isEmpty()) Text("Места появятся сами: побудьте где-то больше часа — Ритм предложит сохранить", style = T.dim)
        places.forEach { p ->
            if (editing?.id == p.id) {
                var name by remember(p.id) { mutableStateOf(p.name) }
                var radius by remember(p.id) { mutableDoubleStateOf(p.radius) }
                var deleted by remember(p.id) { mutableStateOf(false) }
                var gym by remember(p.id) { mutableStateOf(p.isGym) }
                OnLeave {
                    if (!deleted && name.isNotBlank() && (name.trim() != p.name || radius != p.radius || gym != p.isGym)) {
                        PlacesActions.update(context, p.copy(name = name.trim(), radius = radius, isGym = gym))
                    }
                }
                Column(Modifier.padding(vertical = 12.dp)) {
                    BasicTextField(name, { name = it }, textStyle = T.title, cursorBrush = SolidColor(C.accent), singleLine = true)
                    WheelNumber(radius, { radius = it }, 25.0, "радиус, м", style = T.title, min = 100.0, max = 1000.0)
                    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Здесь тренируюсь", style = T.body)
                            Text("Тренировка начнётся сама через 5 минут", style = T.dim)
                        }
                        Switch(gym, { gym = it }, colors = SwitchDefaults.colors(checkedTrackColor = androidx.compose.ui.graphics.Color.White, checkedThumbColor = C.ink, uncheckedTrackColor = C.ghost, uncheckedBorderColor = C.glassLine))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                        Text("Готово", style = T.body.copy(color = C.accent), modifier = Modifier.tap { editing = null }.padding(vertical = 8.dp))
                        Text("Удалить", style = T.body.copy(color = C.dim), modifier = Modifier.tap {
                            deleted = true
                            scope.launch { PlacesActions.delete(context, p); editing = null }
                        }.padding(vertical = 8.dp))
                    }
                }
            } else {
                Item(p.name, "радиус ${p.radius.toInt()} м" + if (p.isGym) " · здесь тренируюсь" else "") { editing = p }
            }
        }
    }
}

@Composable
private fun NewPlacePage(nav: Nav) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val pending = app.day.state.collectAsState().value.pendingPlace
    var name by remember { mutableStateOf("") }
    var done by remember { mutableStateOf(false) }
    OnLeave {
        val p = pending
        if (!done && p != null && name.isNotBlank()) { done = true; PlacesActions.savePending(context, p, name) }
    }
    Page("Как назвать это место?") {
        Chips(listOf("Дом" to "Дом", "Работа" to "Работа", "Зал" to "Зал"), name.takeIf { it in setOf("Дом", "Работа", "Зал") }, { name = it })
        Gap(16.dp)
        BasicTextField(name, { name = it }, textStyle = T.title, cursorBrush = SolidColor(C.accent), singleLine = true,
            decorationBox = { inner -> Box { if (name.isEmpty()) Text("Своё название", style = T.title.copy(color = C.hint)); inner() } })
        Gap(32.dp)
        AccentButton("Сохранить", enabled = name.isNotBlank() && pending != null, onClick = {
            val p = pending ?: return@AccentButton
            done = true
            scope.launch { PlacesActions.savePending(context, p, name); nav.back() }
        })
    }
}

/** Кнопка слева: один раз команда с компьютера, дальше работает сама. Внизу — последние нажатия для проверки. */
@Composable
private fun ButtonPage() {
    val context = LocalContext.current
    val active by LogcatKeySource.active.collectAsState()
    val recent by ButtonBrain.recent.collectAsState()
    val granted = canReadLogs(context)
    Page("Кнопка слева") {
        Text("Удержание — голосовая заметка (только микрофон телефона)\n1 нажатие — то, что сверху: подход, вес или «+»\n2 нажатия — еда\n3 нажатия — отметка момента", style = T.body)
        Gap(28.dp)
        when {
            granted && active -> Text("Работает", style = T.title.copy(color = C.accent))
            granted -> Text("Доступ есть, запускаю…", style = T.title.copy(color = C.dim))
            else -> ReadLogsHint()
        }
        if (recent.isNotEmpty()) {
            Gap(28.dp)
            Text("Последние нажатия", style = T.dim)
            recent.take(8).forEach { Text(it, style = T.dim.copy(color = C.text)) }
        }
    }
    LaunchedEffect(granted) { if (granted) CollectorService.start(context) }
}

/** Как выдать доступ: команда с компьютера + копирование. */
@Composable
fun ReadLogsHint() {
    val context = LocalContext.current
    var copied by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxWidth().glass(18.dp).padding(18.dp)) {
        Text("Один раз с компьютера", style = T.body)
        Text("Подключите телефон по USB (отладка включена) и выполните:", style = T.dim)
        Gap(10.dp)
        Text(READ_LOGS_COMMAND, style = T.dim.copy(color = C.text, fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace))
        Gap(12.dp)
        Text(if (copied) "Скопировано" else "Скопировать команду", style = T.body.copy(color = C.accent), modifier = Modifier.tap {
            val cm = context.getSystemService(android.content.ClipboardManager::class.java)
            cm.setPrimaryClip(android.content.ClipData.newPlainText("adb", READ_LOGS_COMMAND))
            copied = true
        }.padding(vertical = 6.dp))
        Text("После этого ничего перезапускать не нужно — даже после перезагрузки. Ритм берёт из системного журнала только строки про кнопку.", style = T.dim)
    }
}

@Composable
private fun ServerPage(nav: Nav) {
    val app = LocalContext.current.app
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    var pending by remember { mutableIntStateOf(0) }
    var loaded by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { val p = app.repo.settings.get(); url = p.serverUrl; token = p.serverToken; pending = app.repo.db.outbox().pendingCount(); loaded = true }
    OnLeave {
        if (!loaded) return@OnLeave
        val p = app.repo.settings.get()
        if (p.serverUrl != url.trim() || p.serverToken != token.trim()) { app.repo.settings.setServer(url, token); Work.syncNow(app) }
    }
    Page("Сервер") {
        Text("Адрес", style = T.dim)
        BasicTextField(url, { url = it }, textStyle = T.body, cursorBrush = SolidColor(C.accent), singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            decorationBox = { inner -> Box { if (url.isEmpty()) Text("https://…", style = T.body.copy(color = C.hint)); inner() } },
            modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp))
        Text("Ключ", style = T.dim)
        BasicTextField(token, { token = it }, textStyle = T.body, cursorBrush = SolidColor(C.accent), singleLine = true, modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp))
        Gap(16.dp)
        Text("В очереди: $pending", style = T.dim)
        Gap(32.dp)
        AccentButton("Сохранить", onClick = { scope.launch { app.repo.settings.setServer(url, token); Work.syncNow(app); nav.back() } })
    }
}

/** Доступы с галочками: одна фраза «зачем» и кнопка, которая ведёт в нужное место. */
@Composable
fun AccessList(onAllDone: (() -> Unit)? = null) {
    val context = LocalContext.current
    var tick by remember { mutableIntStateOf(0) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    androidx.compose.runtime.DisposableEffect(lifecycle) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) tick++ }
        lifecycle.addObserver(obs)
        onDispose { lifecycle.removeObserver(obs) }
    }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { tick++ }
    val missing = remember(tick) { Permissions.missing(context) }
    LaunchedEffect(tick) {
        if (Permissions.granted(context, Access.LOCATION)) LocationCollector.restart(context)
        if (missing.isEmpty()) onAllDone?.invoke()
    }
    var showButtonHint by remember { mutableStateOf(false) }
    Access.entries.forEach { a ->
        val ok = a !in missing
        Row(Modifier.fillMaxWidth().tap {
            if (ok) return@tap
            val perm = Permissions.runtimePermission(a)
            when {
                a == Access.BUTTON -> showButtonHint = !showButtonHint
                a == Access.LOCATION -> launcher.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
                a == Access.LOCATION_ALWAYS && !Permissions.granted(context, Access.LOCATION) -> launcher.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION))
                perm != null -> launcher.launch(arrayOf(perm))
                else -> context.startActivity(Permissions.settingsIntent(context, a))
            }
        }.padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(if (ok) "✓" else "○", style = T.title.copy(color = if (ok) C.accent else C.dim), modifier = Modifier.padding(end = 16.dp))
            Column {
                Text(a.title, style = T.body.copy(color = if (ok) C.dim else C.text))
                Text(a.why, style = T.dim)
            }
        }
        if (a == Access.BUTTON && !ok && showButtonHint) ReadLogsHint()
    }
}

@Composable
private fun AdvancedPage(nav: Nav) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val prefs by app.repo.settings.flow.collectAsState(initial = null)
    val backupScope = rememberCoroutineScope()
    var backupMsg by remember { mutableStateOf<String?>(null) }
    val restoreLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) backupScope.launch {
            backupMsg = runCatching { Backup.restore(context, uri); "Восстановлено. Ритм закроется — откройте его снова" }
                .getOrElse { "Не получилось: ${it.message}" }
            if (backupMsg!!.startsWith("Восстановлено")) {
                kotlinx.coroutines.delay(1_500)
                (context as? android.app.Activity)?.finishAffinity()
                android.os.Process.killProcess(android.os.Process.myPid())
            }
        }
    }
    Page("Дополнительно") {
        Item("Проверка сбора", "Видно, что шаги, сон, места и кнопка правда собираются") { nav.go(Route.SettingsPage("diag")) }
        TranscriptionItem()
        Item("Сохранить копию", "Всё — в файл в «Загрузки/Ритм». Пригодится при смене телефона") {
            backupScope.launch { backupMsg = runCatching { "Сохранено: Загрузки/Ритм/" + Backup.export(context) }.getOrElse { "Не получилось: ${it.message}" } }
        }
        Item("Восстановить из копии", "Заменит текущие данные данными из файла") { restoreLauncher.launch(arrayOf("application/zip", "application/octet-stream")) }
        backupMsg?.let { Text(it, style = T.dim.copy(color = C.text), modifier = Modifier.padding(vertical = 8.dp)) }
        Gap(16.dp)
        Text("Доступы", style = T.dim)
        AccessList()
        Gap(24.dp)
        val p = prefs ?: return@Page
        Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Калории на экране блокировки", style = T.body)
                Text("Остаток в шторке виден без разблокировки", style = T.dim)
            }
            Switch(p.showOnLockScreen, { scope.launch { app.repo.settings.setShowOnLock(it) } },
                colors = SwitchDefaults.colors(checkedTrackColor = androidx.compose.ui.graphics.Color.White, checkedThumbColor = C.ink, uncheckedTrackColor = C.ghost, uncheckedBorderColor = C.glassLine))
        }
        Gap(16.dp)
        Text("Отдых между подходами", style = T.body)
        WheelNumber(p.restSeconds.toDouble(), { v -> scope.launch { app.repo.settings.setRestSeconds(v.toInt()) } }, 15.0, "секунд", style = T.title, min = 15.0, max = 600.0)
        Gap(16.dp)
        Item("Samsung: не усыплять Ритм", "Батарея → Ограничения в фоне → Ритм не в спящих") {
            context.startActivity(android.content.Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, android.net.Uri.parse("package:${context.packageName}")))
        }
        Gap(16.dp)
        val calib = if (p.calibrated) "Расход откалиброван (×${"%.2f".format(p.calibration)})" else "Расход расчётный — уточнится через 3 недели"
        Text(calib, style = T.dim)
        Text("Версия ${BuildConfigProxy.versionName(context)}", style = T.dim.copy(color = C.hint))
        LaunchedEffect(Unit) { CollectorService.start(context) }
    }
}

private val hm = DateTimeFormatter.ofPattern("HH:mm")

/** Сон под вопросом: подтвердить, поправить границы или сказать «не спал». */
@Composable
private fun SleepPage(nav: Nav) {
    val app = LocalContext.current.app
    val scope = rememberCoroutineScope()
    val s = app.day.state.collectAsState().value.unsureSleep
    if (s == null) { LaunchedEffect(Unit) { nav.back() }; return }
    var start by remember { mutableStateOf(s.interval.start) }
    var end by remember { mutableStateOf(s.interval.end) }
    var decided by remember { mutableStateOf(false) }
    OnLeave {
        if (!decided && (start != s.interval.start || end != s.interval.end)) { decided = true; app.repo.markSleep(start, end, "edited"); app.day.refresh() }
    }
    fun t(ms: Long) = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).format(hm)
    Page("Спал с ${t(start)} до ${t(end)}?") {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(t(start), style = T.big)
                Row { Text("−", style = T.title, modifier = Modifier.tap { start -= 15 * 60_000 }.padding(12.dp)); Text("+", style = T.title, modifier = Modifier.tap { start += 15 * 60_000 }.padding(12.dp)) }
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(t(end), style = T.big)
                Row { Text("−", style = T.title, modifier = Modifier.tap { end -= 15 * 60_000 }.padding(12.dp)); Text("+", style = T.title, modifier = Modifier.tap { end += 15 * 60_000 }.padding(12.dp)) }
            }
        }
        Gap(40.dp)
        AccentButton("Да", onClick = {
            decided = true
            scope.launch {
                val kind = if (start == s.interval.start && end == s.interval.end) "confirmed" else "edited"
                app.repo.markSleep(start, end, kind); app.day.refresh(); nav.back()
            }
        })
        Text("Не спал", style = T.dim, modifier = Modifier.tap {
            decided = true
            scope.launch { app.repo.markSleep(s.interval.start, s.interval.end, "rejected"); app.day.refresh(); nav.back() }
        }.padding(vertical = 20.dp))
    }
}

/** Первый запуск: цели → о себе → доступы → кнопка. Каждый шаг можно пропустить. */
@Composable
fun OnboardingScreen(onFinish: () -> Unit) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    var step by rememberSaveable { mutableIntStateOf(0) }
    fun finish() = scope.launch {
        app.repo.settings.setOnboarded(true)
        CollectorService.start(context)
        LocationCollector.restart(context)
        app.day.refresh()
        onFinish()
    }
    when (step) {
        0 -> GoalsPage(null) { step = 1 }
        1 -> BodyPage(null) { step = 2 }
        2 -> Page("Доступы") {
            Text("Чтобы шаги, сон и места считались сами", style = T.dim)
            Gap(16.dp)
            AccessList()
            Gap(32.dp)
            AccentButton("Дальше", onClick = { step = 3 })
        }
        else -> {
            val granted = canReadLogs(context)
            val recent by ButtonBrain.recent.collectAsState()
            LaunchedEffect(granted) { if (granted) CollectorService.start(context) }
            Page("Кнопка слева") {
                Text(
                    when {
                        !granted -> "Кнопка Bixby станет вашей: удержание — сказать мысль, два нажатия — еда"
                        recent.isEmpty() -> "Нажмите кнопку слева"
                        else -> "Готово. Удерживайте её, чтобы сказать мысль"
                    },
                    style = T.title.copy(color = if (granted && recent.isNotEmpty()) C.accent else C.text),
                )
                Gap(24.dp)
                if (!granted) ReadLogsHint()
                Gap(32.dp)
                AccentButton(if (granted && recent.isNotEmpty()) "Начать" else if (granted) "Пропустить" else "Сделаю позже", onClick = { finish() })
            }
        }
    }
}


/** Проверка сбора: правда ли всё собирается. Зелёная галочка — да, «!» — посмотреть. */
@Composable
private fun DiagPage() {
    val context = LocalContext.current
    val app = context.app
    val state by app.day.state.collectAsState()
    val buttonActive by LogcatKeySource.active.collectAsState()
    val touch by ButtonService.running.collectAsState()
    var rows by remember { mutableStateOf<List<Triple<Boolean, String, String>>>(emptyList()) }
    LaunchedEffect(Unit) {
        while (true) {
            val now = System.currentTimeMillis()
            val db = app.repo.db
            val steps = db.signals().stepsSince(now - 24 * 3600_000L)
            val lastStep = db.signals().lastStepAt()
            val fix = db.places().lastGood()
            val place = fix?.placeId?.let { id -> db.places().places().firstOrNull { it.id == id }?.name }
            val hb = db.outbox().lastOf("heartbeat")
            val pending = db.outbox().pendingCount()
            val prefs = app.repo.settings.get()
            val tick = CollectorService.lastTickAt
            val mic = CollectorService.micPromotedAt
            val missing = Permissions.missing(context)
            fun ago(t: Long?): String = if (t == null || t == 0L) "ещё не было" else when (val m = (now - t) / 60_000) {
                in 0..0 -> "только что"; in 1..59 -> "$m мин назад"; in 60..1439 -> "${m / 60} ч назад"; else -> "${m / 1440} дн назад"
            }
            rows = listOf(
                Triple(tick > 0 && now - tick < 20 * 60_000L, "Фоновая служба", "последний такт ${ago(tick)}"),
                Triple(lastStep != null && now - lastStep < 6 * 3600_000L, "Шаги", "$steps за сутки · последние ${ago(lastStep)}"),
                Triple(state.wakeAt != null, "Сон и день", state.wakeAt?.let { "день начался ${ago(it)}" } ?: "сна ещё не было — нужна ночь данных"),
                Triple(fix != null && now - fix.time < 12 * 3600_000L, "Геопозиция", fix?.let { "точка ${ago(it.time)}" + (place?.let { p -> " · $p" } ?: "") } ?: "точек ещё нет"),
                Triple(buttonActive, "Кнопка слева", if (buttonActive) "слушаю журнал" else if (canReadLogs(context)) "запускается" else "нужна команда с компьютера"),
                Triple(mic > 0, "Голос кнопкой", if (mic > 0) "микрофон готов (${ago(mic)})" else "откройте Ритм после перезагрузки"),
                Triple(touch, "Касания для сна", if (touch) "включено" else "включите в Спецвозможностях"),
                Triple(hb != null && now - hb < 3 * 3600_000L, "Сигнал «жив»", "последний ${ago(hb)}"),
                Triple(prefs.serverUrl.isNotBlank() && pending < 2_000, "Сервер", if (prefs.serverUrl.isBlank()) "не задан — данные копятся на телефоне ($pending)" else "отправлено ${ago(prefs.lastSyncAt)} · в очереди $pending"),
                Triple(missing.isEmpty(), "Доступы", if (missing.isEmpty()) "все есть" else "нет: " + missing.joinToString { it.title.lowercase() }),
            )
            kotlinx.coroutines.delay(5_000)
        }
    }
    Page("Проверка сбора") {
        rows.forEach { (ok, title, detail) ->
            Row(Modifier.fillMaxWidth().padding(vertical = 4.dp).glass(16.dp).padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(if (ok) "✓" else "!", style = T.title.copy(color = if (ok) C.accent else C.over), modifier = Modifier.padding(end = 14.dp))
                Column {
                    Text(title, style = T.body)
                    Text(detail, style = T.dim)
                }
            }
        }
    }
}


/** Расшифровка голоса: модель скачивается один раз (~45 МБ), дальше всё на телефоне без интернета. */
@Composable
private fun TranscriptionItem() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val progress by Transcriber.download.collectAsState()
    var ready by remember { mutableStateOf(Transcriber.ready(context)) }
    val why = when {
        ready -> "Готово: голосовые заметки превращаются в текст прямо на телефоне"
        progress == -1f -> "Не скачалось — нажмите ещё раз (нужен интернет)"
        progress != null -> "Скачиваю… ${((progress ?: 0f) * 100).toInt()}%"
        else -> "Скачать русскую модель (~45 МБ) — потом работает без интернета"
    }
    Item("Голос в текст", why) {
        if (ready || (progress != null && progress != -1f)) return@Item
        scope.launch {
            if (Transcriber.downloadModel(context)) { ready = true; Transcriber.enqueueAll(context) }
        }
    }
}
