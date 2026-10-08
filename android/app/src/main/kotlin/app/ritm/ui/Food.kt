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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.rememberSwipeToDismissBoxState
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
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import app.ritm.app
import app.ritm.data.ComboItem
import app.ritm.data.FoodChoice
import app.ritm.data.FoodRow
import app.ritm.data.ProductRow
import app.ritm.engine.formatInt
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

/** Еда: сверху «обычно сейчас», только названия. Поиск — по касанию. */
@Composable
fun FoodPickScreen(nav: Nav, host: Host, undo: UndoState) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    var query by remember { mutableStateOf("") }
    var choices by remember { mutableStateOf<List<FoodChoice>>(emptyList()) }
    var found by remember { mutableStateOf<List<ProductRow>>(emptyList()) }
    var combo by remember { mutableStateOf<List<ProductRow>?>(null) }

    LaunchedEffect(Unit) {
        val now = System.currentTimeMillis()
        choices = app.repo.foodSuggestions(now, app.day.state.value.wakeAt)
        combo = app.repo.comboSuggestion(now)
    }
    LaunchedEffect(query) {
        found = if (query.length >= 2) app.repo.searchProducts(query) else emptyList()
    }

    Column(Modifier.fillMaxSize().statusBarsPadding().imePadding().padding(horizontal = 24.dp)) {
        Gap(24.dp)
        BasicTextField(
            value = query,
            onValueChange = { query = it },
            textStyle = T.title,
            cursorBrush = SolidColor(C.accent),
            singleLine = true,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            decorationBox = { inner -> Box { if (query.isEmpty()) Text("Найти…", style = T.title.copy(color = C.faint)); inner() } },
            modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp),
        )
        Gap(16.dp)
        LazyColumn(Modifier.weight(1f)) {
            if (query.length >= 2) {
                items(found, key = { "f${it.id}" }) { p -> FoodRowLine(p.name) { nav.go(Route.FoodAmount(p.id)) } }
                item {
                    Text("Добавить «${query.trim()}»", style = T.body.copy(color = C.accent),
                        modifier = Modifier.fillMaxWidth().tap { nav.go(Route.AddProduct(query.trim())) }.padding(vertical = 16.dp))
                }
            } else {
                combo?.let { c ->
                    item {
                        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text("Сохранить «${c.joinToString(" + ") { it.name.lowercase() }}» как комплект?", style = T.dim, modifier = Modifier.weight(1f))
                            Text("Да", style = T.body.copy(color = C.accent), modifier = Modifier.tap {
                                scope.launch {
                                    val since = app.day.state.value.wakeAt ?: (System.currentTimeMillis() - 86_400_000)
                                    val items: List<ComboItem> = app.repo.lastGramsToday(c.map { it.id }, since)
                                    app.repo.saveCombo(c.first().name, items)
                                    combo = null
                                    choices = app.repo.foodSuggestions(System.currentTimeMillis(), app.day.state.value.wakeAt)
                                }
                            }.padding(8.dp))
                            Text("Нет", style = T.dim, modifier = Modifier.tap {
                                scope.launch { app.repo.dismissComboSuggestion(c.map { it.id }); combo = null }
                            }.padding(8.dp))
                        }
                    }
                }
                if (choices.isEmpty()) item {
                    Text("Начните вводить название — частое появится здесь само", style = T.dim, modifier = Modifier.padding(vertical = 16.dp))
                }
                items(choices, key = { c -> if (c is FoodChoice.Combo) "c${c.row.id}" else "p${(c as FoodChoice.Product).row.id}" }) { c ->
                    when (c) {
                        is FoodChoice.Combo -> FoodRowLine("★ ${c.title}") {
                            scope.launch {
                                val rows = app.repo.addCombo(c.row)
                                app.day.refresh()
                                undo.show("Записано") { scope.launch { rows.forEach { app.repo.deleteFood(it) }; app.day.refresh() } }
                                host.done()
                            }
                        }
                        is FoodChoice.Product -> FoodRowLine(c.title) { nav.go(Route.FoodAmount(c.row.id)) }
                    }
                }
            }
        }
    }
}

@Composable
private fun FoodRowLine(title: String, onClick: () -> Unit) {
    Text(title, style = T.title, modifier = Modifier.fillMaxWidth().tap(onClick).padding(vertical = 14.dp))
}

/** Одна цифра — граммы. Подставлен мой прошлый раз или обычная порция. */
@Composable
fun FoodAmountScreen(nav: Nav, host: Host, undo: UndoState, productId: Long, editFoodId: Long?) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    var product by remember { mutableStateOf<ProductRow?>(null) }
    var editing by remember { mutableStateOf<FoodRow?>(null) }
    var grams by remember { mutableDoubleStateOf(100.0) }
    var minutesAgo by remember { mutableIntStateOf(0) }

    LaunchedEffect(productId, editFoodId) {
        val p = app.repo.db.food().product(productId)
        product = p
        val wake = app.day.state.value.wakeAt ?: 0
        val e = editFoodId?.let { id -> app.repo.db.food().food(wake, System.currentTimeMillis() + 1).firstOrNull { it.id == id } }
        editing = e
        grams = e?.grams ?: p?.let { app.repo.defaultGrams(it) } ?: 100.0
    }
    val p = product ?: return
    Column(Modifier.fillMaxSize().navigationBarsPadding().imePadding(), horizontalAlignment = Alignment.CenterHorizontally) {
        Gap(96.dp)
        Text(p.name, style = T.title)
        if (editing == null) {
            val labels = listOf(0 to "сейчас", 30 to "30 мин назад", 60 to "час назад", 120 to "2 часа назад")
            Text(labels.first { it.first == minutesAgo }.second, style = T.dim, modifier = Modifier.tap {
                val i = labels.indexOfFirst { it.first == minutesAgo }
                minutesAgo = labels[(i + 1) % labels.size].first
            }.padding(8.dp))
        }
        Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
            WheelNumber(grams, { grams = it }, step = if (grams >= 100) 10.0 else 5.0, unit = "г", min = 1.0, max = 3000.0, style = T.huge)
        }
        Text("${(p.kcal100 * grams / 100).roundToInt()} ккал", style = T.dim)
        Gap(24.dp)
        AccentButton(if (editing != null) "Сохранить" else "Записать", onClick = {
            scope.launch {
                val e = editing
                if (e != null) {
                    app.repo.updateFoodGrams(e, grams)
                    app.day.refresh()
                    nav.back()
                } else {
                    val row = app.repo.addFood(p, grams, System.currentTimeMillis() - minutesAgo * 60_000L)
                    app.day.refresh()
                    undo.show("Записано · ${p.name.lowercase()}") { scope.launch { app.repo.deleteFood(row); app.day.refresh() } }
                    host.done()
                }
            }
        })
        Gap(48.dp)
    }
}

/** Свой продукт: название и калорийность на 100 г. Остальное — по желанию. */
@Composable
fun AddProductScreen(nav: Nav, name: String) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    var title by remember { mutableStateOf(name.replaceFirstChar { it.uppercase() }) }
    var kcal by remember { mutableDoubleStateOf(100.0) }
    var more by remember { mutableStateOf(false) }
    var protein by remember { mutableDoubleStateOf(0.0) }
    var fat by remember { mutableDoubleStateOf(0.0) }
    var carbs by remember { mutableDoubleStateOf(0.0) }
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding().padding(horizontal = 24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Gap(48.dp)
        BasicTextField(title, { title = it }, textStyle = T.title, cursorBrush = SolidColor(C.accent), singleLine = true, modifier = Modifier.fillMaxWidth())
        Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                WheelNumber(kcal, { kcal = it }, step = 5.0, unit = "ккал на 100 г", max = 950.0)
                if (more) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        WheelNumber(protein, { protein = it }, 0.5, "белки", decimals = 1, style = T.title, max = 100.0)
                        WheelNumber(fat, { fat = it }, 0.5, "жиры", decimals = 1, style = T.title, max = 100.0)
                        WheelNumber(carbs, { carbs = it }, 0.5, "углеводы", decimals = 1, style = T.title, max = 100.0)
                    }
                } else {
                    Text("Белки, жиры, углеводы", style = T.dim, modifier = Modifier.tap { more = true }.padding(16.dp))
                }
            }
        }
        AccentButton("Добавить", enabled = title.isNotBlank(), onClick = {
            scope.launch {
                val p = app.repo.addOwnProduct(title, kcal, protein.takeIf { more }, fat.takeIf { more }, carbs.takeIf { more })
                nav.replace(Route.FoodAmount(p.id))
            }
        })
        Gap(48.dp)
    }
}

/** Еда за день — по касанию цифры. Свайп влево — удалить, касание — изменить граммы. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FoodTodayScreen(nav: Nav, undo: UndoState) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    val state by app.day.state.collectAsState()
    val wake = state.wakeAt
    val food by remember(wake) { if (wake != null) app.repo.foodSince(wake) else flowOf(emptyList()) }.collectAsState(initial = emptyList())
    Box(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        Column(Modifier.fillMaxSize().padding(horizontal = 24.dp)) {
            Gap(32.dp)
            val plan = state.plan
            Text(if (plan != null) "${formatInt(state.eaten)} из ${formatInt(plan)}" else formatInt(state.eaten), style = T.title)
            Gap(24.dp)
            LazyColumn(Modifier.weight(1f)) {
                if (food.isEmpty()) item { Text("Сегодня ещё ничего не записано", style = T.dim) }
                items(food.reversed(), key = { it.id }) { f ->
                    val dismiss = rememberSwipeToDismissBoxState(confirmValueChange = { v ->
                        if (v == SwipeToDismissBoxValue.EndToStart) {
                            scope.launch {
                                app.repo.deleteFood(f); app.day.refresh()
                                undo.show("Удалено") { scope.launch { app.repo.restoreFood(f); app.day.refresh() } }
                            }
                            true
                        } else false
                    })
                    SwipeToDismissBox(dismiss, backgroundContent = {}, enableDismissFromStartToEnd = false) {
                        Row(Modifier.fillMaxWidth().tap { f.productId?.let { nav.go(Route.FoodAmount(it, f.id)) } }.padding(vertical = 14.dp)) {
                            Text(f.name, style = T.body, modifier = Modifier.weight(1f))
                            Text("${f.grams.roundToInt()} г · ${f.kcal.roundToInt()}", style = T.dim)
                        }
                    }
                }
            }
        }
        UndoBar(undo, Modifier.align(Alignment.BottomCenter).padding(bottom = 24.dp))
    }
}
