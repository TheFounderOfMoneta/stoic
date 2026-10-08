package app.ritm.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.runtime.saveable.rememberSaveable
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
import app.ritm.food.BarcodeScanner
import app.ritm.food.OffProduct
import app.ritm.food.OpenFoodFacts
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
    var online by remember { mutableStateOf<List<OffProduct>>(emptyList()) }
    var onlineState by remember { mutableStateOf("") }
    var scanMsg by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(query) {
        found = if (query.length >= 2) app.repo.searchProducts(query) else emptyList()
        online = emptyList(); onlineState = ""
        // Своей базы мало — после паузы в наборе ищем в открытой базе продуктов.
        if (query.trim().length >= 3) {
            kotlinx.coroutines.delay(700)
            onlineState = "Ищу в интернете…"
            online = OpenFoodFacts.search(query)
            onlineState = if (online.isEmpty()) "В интернете не нашлось" else ""
        }
    }
    fun scan() = scope.launch {
        scanMsg = null
        val code = BarcodeScanner.scan(context) ?: return@launch
        app.repo.productByBarcode(code)?.let { nav.go(Route.FoodAmount(it.id)); return@launch }
        scanMsg = "Ищу штрихкод $code…"
        val off = OpenFoodFacts.byBarcode(code)
        if (off != null) { scanMsg = null; nav.go(Route.FoodAmount(app.repo.saveOffProduct(off).id)) }
        else scanMsg = "Штрихкод $code не нашёлся — добавьте продукт вручную"
    }

    val day by app.day.state.collectAsState()
    Box(Modifier.fillMaxSize()) {
    Column(Modifier.fillMaxSize().statusBarsPadding().imePadding().padding(horizontal = 24.dp)) {
        Gap(16.dp)
        // Сколько осталось — видно сразу после записи, можно добавлять следующее.
        day.remaining?.let { r ->
            Text(if (r >= 0) "Осталось ${formatInt(r)} ккал" else "Сверх ${formatInt(-r)} ккал", style = T.dim.copy(color = C.text))
        }
        Gap(8.dp)
        BasicTextField(
            value = query,
            onValueChange = { query = it },
            textStyle = T.title,
            cursorBrush = SolidColor(C.accent),
            singleLine = true,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            decorationBox = { inner -> Box { if (query.isEmpty()) Text("Найти…", style = T.title.copy(color = C.hint)); inner() } },
            modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp),
        )
        Text("Сканировать штрихкод", style = T.body.copy(color = C.accent), modifier = Modifier.tap { scan() }.padding(vertical = 6.dp))
        scanMsg?.let { Text(it, style = T.dim) }
        Gap(10.dp)
        LazyColumn(Modifier.weight(1f)) {
            if (query.length >= 2) {
                items(found, key = { "f${it.id}" }) { p -> FoodRowLine(p.name) { nav.go(Route.FoodAmount(p.id)) } }
                if (online.isNotEmpty()) {
                    item(key = "h-online") { Text("Из интернета (Open Food Facts)", style = T.dim, modifier = Modifier.padding(top = 12.dp, bottom = 4.dp)) }
                    items(online, key = { "o${it.barcode}${it.title}" }) { o ->
                        Row(Modifier.fillMaxWidth().tap { scope.launch { nav.go(Route.FoodAmount(app.repo.saveOffProduct(o).id)) } }.padding(vertical = 12.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            Text(o.title, style = T.body, modifier = Modifier.weight(1f), maxLines = 2)
                            Text("${o.kcal.roundToInt()} ккал", style = T.dim)
                        }
                    }
                }
                if (onlineState.isNotEmpty()) item(key = "s-online") { Text(onlineState, style = T.dim.copy(color = C.hint), modifier = Modifier.padding(vertical = 8.dp)) }
                item {
                    Text("+ Добавить «${query.trim()}» вручную", style = T.body.copy(color = C.accent),
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
                                undo.show("Записано · ${c.title.lowercase()}") { scope.launch { rows.forEach { app.repo.deleteFood(it) }; app.day.refresh() } }
                            }
                        }
                        is FoodChoice.Product -> FoodRowLine(c.title) { nav.go(Route.FoodAmount(c.row.id)) }
                    }
                }
            }
        }
    }
    UndoBar(undo, Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = 16.dp))
    }
}

@Composable
private fun FoodRowLine(title: String, onClick: () -> Unit) {
    Text(title, style = T.row, modifier = Modifier.fillMaxWidth().tap(onClick).padding(vertical = 14.dp))
}

/** Одна цифра — граммы. Подставлен мой прошлый раз или обычная порция. */
@Composable
fun FoodAmountScreen(nav: Nav, host: Host, undo: UndoState, productId: Long, editFoodId: Long?) {
    val context = LocalContext.current
    val app = context.app
    val scope = rememberCoroutineScope()
    var product by remember { mutableStateOf<ProductRow?>(null) }
    var editing by remember { mutableStateOf<FoodRow?>(null) }
    var grams by rememberSaveable { mutableDoubleStateOf(100.0) }
    var minutesAgo by rememberSaveable { mutableIntStateOf(0) }
    var initialGrams by rememberSaveable { mutableStateOf<Double?>(null) }
    var done by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(productId, editFoodId) {
        val p = app.repo.db.food().product(productId)
        product = p
        val wake = app.day.state.value.wakeAt ?: 0
        val e = editFoodId?.let { id -> app.repo.db.food().food(wake, System.currentTimeMillis() + 1).firstOrNull { it.id == id } }
        editing = e
        if (initialGrams == null) grams = e?.grams ?: p?.let { app.repo.defaultGrams(it) } ?: 100.0
    }
    LaunchedEffect(product) { if (product != null && initialGrams == null) initialGrams = grams }
    // Поменял граммы и вышел — записать (или сохранить правку).
    OnLeave {
        val pr = product ?: return@OnLeave
        val start = initialGrams ?: return@OnLeave
        if (done || kotlin.math.abs(grams - start) < 0.01) return@OnLeave
        val e = editing
        done = true
        if (e != null) app.repo.updateFoodGrams(e, grams) else app.repo.addFood(pr, grams, System.currentTimeMillis() - minutesAgo * 60_000L)
        app.day.refresh()
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
        Text("${(p.kcal100 * grams / 100).roundToInt()} ккал", style = T.body)
        macros(p, grams)?.let { Text(it, style = T.dim) }
        Text("изменить продукт", style = T.dim.copy(color = C.accent), modifier = Modifier.tap { nav.go(Route.EditProduct(p.id)) }.padding(8.dp))
        Gap(24.dp)
        AccentButton(if (editing != null) "Сохранить" else "Записать", onClick = {
            done = true
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
                    host.done() // назад в список еды — можно сразу добавить следующее
                }
            }
        })
        Gap(48.dp)
    }
}

/** Значения продукта на 100 г. */
private data class ProductValues(val name: String, val kcal: Double, val protein: Double, val fat: Double, val carbs: Double)

/**
 * Форма продукта: название, калории и БЖУ на 100 г — все четыре строки сразу.
 * Пока калории не трогали руками, они считаются из БЖУ: 4·Б + 9·Ж + 4·У.
 */
@Composable
private fun ProductForm(
    title: String,
    initial: ProductValues,
    button: String,
    onChange: (ProductValues) -> Unit,
    onSubmit: () -> Unit,
) {
    var v by remember(initial) { mutableStateOf(initial) }
    var kcalTouched by remember(initial) { mutableStateOf(initial.kcal > 0) }
    fun update(n: ProductValues) {
        val auto = if (!kcalTouched && (n.protein + n.fat + n.carbs) > 0) n.copy(kcal = Math.round(4 * n.protein + 9 * n.fat + 4 * n.carbs).toDouble()) else n
        v = auto; onChange(auto)
    }
    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp)) {
        Gap(32.dp)
        Text(title, style = T.title)
        Gap(16.dp)
        BasicTextField(v.name, { update(v.copy(name = it)) }, textStyle = T.body, cursorBrush = SolidColor(C.accent), singleLine = true,
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            decorationBox = { inner -> Box { if (v.name.isEmpty()) Text("Название", style = T.body.copy(color = C.hint)); inner() } },
            modifier = Modifier.fillMaxWidth().glass(16.dp).padding(horizontal = 16.dp, vertical = 14.dp))
        Gap(18.dp)
        Text("На 100 г", style = T.dim)
        Gap(6.dp)
        NutrientRow("Калории") { WheelNumber(v.kcal, { kcalTouched = true; update(v.copy(kcal = it)) }, 5.0, "ккал", style = T.title, max = 950.0) }
        NutrientRow("Белки") { WheelNumber(v.protein, { update(v.copy(protein = it)) }, 0.5, "г", decimals = 1, style = T.title, max = 100.0) }
        NutrientRow("Жиры") { WheelNumber(v.fat, { update(v.copy(fat = it)) }, 0.5, "г", decimals = 1, style = T.title, max = 100.0) }
        NutrientRow("Углеводы") { WheelNumber(v.carbs, { update(v.copy(carbs = it)) }, 0.5, "г", decimals = 1, style = T.title, max = 100.0) }
        if (!kcalTouched) Text("Калории посчитаются из БЖУ сами — или введите вручную", style = T.dim.copy(color = C.hint), modifier = Modifier.padding(top = 8.dp))
        Gap(28.dp)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
            AccentButton(button, enabled = v.name.isNotBlank(), onClick = onSubmit)
        }
        Gap(32.dp)
    }
}

@Composable
private fun NutrientRow(label: String, content: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp).glass(18.dp).padding(start = 18.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = T.body, modifier = Modifier.weight(1f))
        content()
    }
}

/** Свой продукт. Ввёл и вышел — всё равно сохранится. */
@Composable
fun AddProductScreen(nav: Nav, name: String) {
    val app = LocalContext.current.app
    val scope = rememberCoroutineScope()
    val initial = remember { ProductValues(name.replaceFirstChar { it.uppercase() }, 0.0, 0.0, 0.0, 0.0) }
    var values by remember { mutableStateOf(initial) }
    var created by remember { mutableStateOf(false) }
    suspend fun create() = app.repo.addOwnProduct(values.name, values.kcal, values.protein.takeIf { it > 0 }, values.fat.takeIf { it > 0 }, values.carbs.takeIf { it > 0 })
    OnLeave { if (!created && values.name.isNotBlank() && values.kcal > 0) { created = true; create() } }
    ProductForm("Новый продукт", initial, "Добавить", onChange = { values = it }, onSubmit = {
        created = true
        scope.launch { val p = create(); nav.replace(Route.FoodAmount(p.id)) }
    })
}

/** Правка продукта: калории и БЖУ. Сохраняется само при выходе. */
@Composable
fun EditProductScreen(nav: Nav, productId: Long) {
    val app = LocalContext.current.app
    val scope = rememberCoroutineScope()
    var product by remember { mutableStateOf<ProductRow?>(null) }
    LaunchedEffect(productId) { product = app.repo.product(productId) }
    val p = product ?: return
    val initial = remember(p.id) { ProductValues(p.name, p.kcal100, p.protein ?: 0.0, p.fat ?: 0.0, p.carbs ?: 0.0) }
    var values by remember(p.id) { mutableStateOf(initial) }
    var lastSaved by remember(p.id) { mutableStateOf(initial) }
    suspend fun save() {
        val v = values
        if (v == lastSaved || v.name.isBlank()) return
        lastSaved = v
        app.repo.updateProduct(p, v.name, v.kcal, v.protein.takeIf { it > 0 }, v.fat.takeIf { it > 0 }, v.carbs.takeIf { it > 0 })
    }
    OnLeave { save() }
    ProductForm("Продукт", initial, "Сохранить", onChange = { values = it }, onSubmit = {
        scope.launch { save(); nav.back() }
    })
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
            var products by remember { mutableStateOf<Map<Long, ProductRow>>(emptyMap()) }
            LaunchedEffect(food) { products = app.repo.db.food().products(food.mapNotNull { it.productId }.distinct()).associateBy { it.id } }
            val totals = food.mapNotNull { f -> products[f.productId]?.let { it to f.grams } }
            if (totals.any { (p, _) -> p.protein != null || p.fat != null || p.carbs != null }) {
                fun sum(sel: (ProductRow) -> Double?) = totals.sumOf { (p, g) -> (sel(p) ?: 0.0) * g / 100 }.roundToInt()
                Text("Б ${sum { it.protein }} · Ж ${sum { it.fat }} · У ${sum { it.carbs }} г", style = T.dim)
            }
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
                            Text("${f.grams.roundToInt()} г · ${f.kcal.roundToInt()} ккал", style = T.dim)
                        }
                    }
                }
            }
        }
        UndoBar(undo, Modifier.align(Alignment.BottomCenter).padding(bottom = 24.dp))
    }
}

/** «Б 12 · Ж 3 · У 40 г» на порцию, если у продукта есть БЖУ. */
private fun macros(p: ProductRow, grams: Double): String? {
    if (p.protein == null && p.fat == null && p.carbs == null) return null
    fun v(x: Double?) = ((x ?: 0.0) * grams / 100).roundToInt()
    return "Б ${v(p.protein)} · Ж ${v(p.fat)} · У ${v(p.carbs)} г"
}
