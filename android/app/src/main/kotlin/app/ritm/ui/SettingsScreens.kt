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
import app.ritm.button.LogcatKeySource
import app.ritm.button.READ_LOGS_COMMAND
import app.ritm.button.canReadLogs
import app.ritm.collect.CollectorService
import app.ritm.collect.LocationCollector
import app.ritm.collect.PlacesActions
import app.ritm.core.energy.Body
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
        Item("Тело и норма", "Пол, рост, возраст, цель — отсюда норма калорий") { nav.go(Route.SettingsPage("body")) }
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
        "advanced" -> AdvancedPage()
        "sleep" -> SleepPage(nav)
    }
}

@Composable
fun GoalsPage(nav: Nav?, onNext: (() -> Unit)? = null) {
    val app = LocalContext.current.app
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    LaunchedEffect(Unit) { text = app.repo.settings.get().goals }
    Page("Чего хотите достичь?") {
        BasicTextField(text, { text = it }, textStyle = T.body, cursorBrush = SolidColor(C.accent),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            decorationBox = { inner -> Box { if (text.isEmpty()) Text("Своими словами. Например: похудеть до 80 кг, выучить английский", style = T.body.copy(color = C.hint)); inner() } },
            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        Gap(32.dp)
        AccentButton(if (onNext != null) "Дальше" else "Готово", onClick = {
            scope.launch {
                app.repo.settings.setGoals(text.trim())
                app.repo.events.emit("profile.goals") { put("text", text.trim()) }
                onNext?.invoke() ?: nav?.back()
            }
        })
        if (onNext != null) Text("Пропустить", style = T.dim, modifier = Modifier.tap { onNext() }.padding(vertical = 16.dp))
    }
}


/** Пол, рост, дата рождения, вес, цель и темп — на одном экране; внизу норма. */
@Composable
fun BodyPage(nav: Nav?, onNext: (() -> Unit)? = null) {
    val app = LocalContext.current.app
    val scope = rememberCoroutineScope()
    var sex by remember { mutableStateOf<Sex?>(null) }
    var height by remember { mutableDoubleStateOf(175.0) }
    var birthYear by remember { mutableIntStateOf(1995) }
    var weight by remember { mutableDoubleStateOf(75.0) }
    var goal by remember { mutableStateOf(GoalType.LOSE) }
    var pace by remember { mutableDoubleStateOf(0.5) }
    var hadWeight by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        val p = app.repo.settings.get()
        sex = p.sex; p.heightCm?.let { height = it }; p.birthDate?.let { birthYear = it.year }
        goal = p.goalType; pace = p.paceKgPerWeek
        app.repo.lastWeight()?.let { weight = it.kg; hadWeight = true }
    }
    val birth = LocalDate.of(birthYear, 7, 1)
    val norm = sex?.let { dailyPlan(Body(it, height, birth), weight, LocalDate.now(), WeightGoal(goal, pace), null) }
    Page(if (onNext != null) "О себе" else "Тело и норма") {
        Chips(listOf(Sex.MALE to "Мужчина", Sex.FEMALE to "Женщина"), sex, { sex = it })
        Gap(24.dp)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            WheelNumber(height, { height = it }, 1.0, "рост, см", style = T.title, min = 120.0, max = 230.0)
            WheelNumber(birthYear.toDouble(), { birthYear = it.toInt() }, 1.0, "год рождения", style = T.title, min = 1930.0, max = LocalDate.now().year - 10.0)
            WheelNumber(weight, { weight = it }, 0.1, "вес, кг", decimals = 1, style = T.title, min = 30.0, max = 250.0, stepDp = 14.dp)
        }
        Gap(32.dp)
        Chips(listOf(GoalType.LOSE to "Снизить", GoalType.KEEP to "Держать", GoalType.GAIN to "Набрать"), goal, { goal = it })
        if (goal != GoalType.KEEP) {
            Gap(12.dp)
            Chips(listOf(0.25 to "0,25", 0.5 to "0,5", 0.75 to "0,75"), pace, { pace = it })
            Text("кг в неделю", style = T.dim, modifier = Modifier.padding(start = 4.dp, top = 6.dp))
        }
        Gap(32.dp)
        if (norm != null) Text("≈ ${formatInt(norm)} ккал в день", style = T.title.copy(color = C.accent))
        Gap(32.dp)
        AccentButton(if (onNext != null) "Дальше" else "Готово", enabled = sex != null, onClick = {
            val s = sex ?: return@AccentButton
            scope.launch {
                app.repo.settings.setBody(s, height, birth)
                app.repo.settings.setGoal(goal, pace)
                if (!hadWeight || app.repo.lastWeight()?.kg != weight) app.repo.addWeight(weight)
                app.repo.events.emit("profile.body") {
                    put("sex", s.name); put("heightCm", height); put("birthYear", birthYear)
                    put("goal", goal.name); put("pace", pace)
                }
                app.day.refresh()
                onNext?.invoke() ?: nav?.back()
            }
        })
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
                Column(Modifier.padding(vertical = 12.dp)) {
                    BasicTextField(name, { name = it }, textStyle = T.title, cursorBrush = SolidColor(C.accent), singleLine = true)
                    WheelNumber(radius, { radius = it }, 25.0, "радиус, м", style = T.title, min = 100.0, max = 1000.0)
                    Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                        Text("Сохранить", style = T.body.copy(color = C.accent), modifier = Modifier.tap {
                            scope.launch { PlacesActions.update(context, p.copy(name = name.trim(), radius = radius)); editing = null }
                        }.padding(vertical = 8.dp))
                        Text("Удалить", style = T.body.copy(color = C.dim), modifier = Modifier.tap {
                            scope.launch { PlacesActions.delete(context, p); editing = null }
                        }.padding(vertical = 8.dp))
                    }
                }
            } else {
                Item(p.name, "радиус ${p.radius.toInt()} м") { editing = p }
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
    Page("Как назвать это место?") {
        Chips(listOf("Дом" to "Дом", "Работа" to "Работа", "Зал" to "Зал"), name.takeIf { it in setOf("Дом", "Работа", "Зал") }, { name = it })
        Gap(16.dp)
        BasicTextField(name, { name = it }, textStyle = T.title, cursorBrush = SolidColor(C.accent), singleLine = true,
            decorationBox = { inner -> Box { if (name.isEmpty()) Text("Своё название", style = T.title.copy(color = C.hint)); inner() } })
        Gap(32.dp)
        AccentButton("Сохранить", enabled = name.isNotBlank() && pending != null, onClick = {
            val p = pending ?: return@AccentButton
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
    LaunchedEffect(Unit) { val p = app.repo.settings.get(); url = p.serverUrl; token = p.serverToken; pending = app.repo.db.outbox().pendingCount() }
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
private fun AdvancedPage() {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val prefs by app.repo.settings.flow.collectAsState(initial = null)
    Page("Дополнительно") {
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
            scope.launch {
                val kind = if (start == s.interval.start && end == s.interval.end) "confirmed" else "edited"
                app.repo.markSleep(start, end, kind); app.day.refresh(); nav.back()
            }
        })
        Text("Не спал", style = T.dim, modifier = Modifier.tap {
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
    var step by remember { mutableIntStateOf(0) }
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
