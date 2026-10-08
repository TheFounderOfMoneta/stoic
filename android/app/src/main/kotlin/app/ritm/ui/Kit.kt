package app.ritm.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.border
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.foundation.layout.fillMaxSize
import app.ritm.R
import app.ritm.app
import kotlinx.coroutines.launch
import androidx.compose.animation.core.animateIntAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.widthIn
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.util.Locale
import kotlin.math.roundToInt

/** Палитра дня: утро, день, вечер, ночь. Фон и акценты меняются вместе со временем суток. */
data class Palette(val a1: Color, val a2: Color, val base: Color, val tint: Color)

object Palettes {
    val morning = Palette(Color(0xFFFFB38A), Color(0xFFFF6F91), Color(0xFF2A1838), Color(0xFFFFD3BE))
    val day = Palette(Color(0xFF5EE7C8), Color(0xFF3B82F6), Color(0xFF0C1D36), Color(0xFFA8F0DF))
    val evening = Palette(Color(0xFFFF7A59), Color(0xFF8B5CF6), Color(0xFF1A1033), Color(0xFFFFC4AE))
    val night = Palette(Color(0xFF4F46E5), Color(0xFF0EA5E9), Color(0xFF070B1C), Color(0xFFB4CCFF))

    fun forHour(h: Int): Palette = when (h) {
        in 5..10 -> morning
        in 11..16 -> day
        in 17..21 -> evening
        else -> night
    }
}

/** Текущая палитра (обновляется раз в минуту). Чтение в композиции подписывает на смену. */
object Ambient {
    var palette by mutableStateOf(Palettes.forHour(java.time.LocalTime.now().hour))
        private set

    fun refresh() { palette = Palettes.forHour(java.time.LocalTime.now().hour) }
}

/** Цвета интерфейса поверх живого фона. */
object C {
    val bg: Color get() = Ambient.palette.base
    val text = Color(0xFFFFFFFF)
    val dim = Color(0xB3FFFFFF)
    val hint = Color(0x73FFFFFF)
    val faint = Color(0x24FFFFFF)
    /** Стекло: полупрозрачный белый. */
    val ghost = Color(0x17FFFFFF)
    val glassLine = Color(0x29FFFFFF)
    /** Акцент для ссылок и галочек — светлый оттенок палитры, читается на любом фоне. */
    val accent: Color get() = Ambient.palette.tint
    val glow: Color get() = Ambient.palette.a1
    val ink = Color(0xFF111114)
    val over = Color(0xFFFF8A7A)
}

@OptIn(ExperimentalTextApi::class)
private fun unbounded(w: Int) = Font(R.font.unbounded, FontWeight(w), variationSettings = FontVariation.Settings(FontVariation.weight(w)))

@OptIn(ExperimentalTextApi::class)
private fun manrope(w: Int) = Font(R.font.manrope, FontWeight(w), variationSettings = FontVariation.Settings(FontVariation.weight(w)))

/** Unbounded — цифры и заголовки, Manrope — текст. */
val Display = FontFamily(unbounded(300), unbounded(400), unbounded(500))
val Body = FontFamily(manrope(300), manrope(400), manrope(500), manrope(600))

/** Два шрифта, четыре размера. Цифры моноширинные — не прыгают при смене. */
object T {
    private const val tnum = "tnum"
    val huge = TextStyle(fontFamily = Display, fontSize = 60.sp, fontWeight = FontWeight(300), fontFeatureSettings = tnum, color = C.text, letterSpacing = (-1.5).sp)
    val big = TextStyle(fontFamily = Display, fontSize = 52.sp, fontWeight = FontWeight(300), fontFeatureSettings = tnum, color = C.text, letterSpacing = (-1).sp)
    val title = TextStyle(fontFamily = Display, fontSize = 20.sp, fontWeight = FontWeight(300), color = C.text)
    val body = TextStyle(fontFamily = Body, fontSize = 16.sp, fontWeight = FontWeight(500), color = C.text)
    val row = TextStyle(fontFamily = Body, fontSize = 19.sp, fontWeight = FontWeight(500), color = C.text)
    val dim = TextStyle(fontFamily = Body, fontSize = 13.sp, fontWeight = FontWeight(500), color = C.dim)
}

@Composable
fun RitmTheme(content: @Composable () -> Unit) {
    val p = Ambient.palette
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = Color.White, onPrimary = C.ink, background = p.base, onBackground = C.text,
            surface = p.base, onSurface = C.text, surfaceVariant = C.ghost, onSurfaceVariant = C.dim,
            surfaceContainer = p.base, surfaceContainerHigh = p.base, surfaceContainerHighest = p.base, surfaceContainerLow = p.base,
            outline = C.faint, secondary = C.dim, primaryContainer = p.a2, onPrimaryContainer = Color.White,
        ),
        typography = androidx.compose.material3.Typography().let { t ->
            t.copy(
                bodyLarge = t.bodyLarge.copy(fontFamily = Body), bodyMedium = t.bodyMedium.copy(fontFamily = Body),
                labelLarge = t.labelLarge.copy(fontFamily = Body), titleLarge = t.titleLarge.copy(fontFamily = Display),
                headlineLarge = t.headlineLarge.copy(fontFamily = Display), displayLarge = t.displayLarge.copy(fontFamily = Display),
            )
        },
        content = content,
    )
}

/**
 * Живой фон: три мягких пятна цвета палитры медленно плывут.
 * Перерисовка ~15 раз в секунду — плавно, но без лишнего расхода батареи.
 */
@Composable
fun AmbientBackground(modifier: Modifier = Modifier) {
    val p = Ambient.palette
    val a1 by animateColorAsState(p.a1, tween(1500), label = "a1")
    val a2 by animateColorAsState(p.a2, tween(1500), label = "a2")
    val base by animateColorAsState(p.base, tween(1500), label = "base")
    var phase by remember { mutableFloatStateOf(0f) }
    LaunchedEffect(Unit) {
        val start = System.nanoTime()
        while (true) {
            phase = ((System.nanoTime() - start) / 1e9f) / 18f
            kotlinx.coroutines.delay(66)
        }
    }
    LaunchedEffect(Unit) { while (true) { Ambient.refresh(); kotlinx.coroutines.delay(60_000) } }
    Canvas(modifier.fillMaxSize()) {
        val w = size.width; val h = size.height
        val s = kotlin.math.sin(phase * 2 * Math.PI).toFloat()
        val c = kotlin.math.cos(phase * 2 * Math.PI).toFloat()
        drawRect(base)
        fun blob(color: Color, alpha: Float, x: Float, y: Float, r: Float) {
            val center = Offset(x, y)
            drawCircle(Brush.radialGradient(listOf(color.copy(alpha = alpha), color.copy(alpha = 0f)), center, r), r, center)
        }
        blob(a1, 0.62f, w * (0.28f + 0.06f * s), h * (0.20f + 0.03f * c), w * 0.85f)
        blob(a2, 0.55f, w * (0.80f - 0.05f * c), h * (0.42f + 0.04f * s), w * 0.9f)
        blob(a2, 0.45f, w * (0.50f + 0.04f * s), h * (1.02f), w * 1.05f)
        // Лёгкое затемнение — текст всегда читается.
        drawRect(Color.Black.copy(alpha = 0.18f))
    }
}

/** Стеклянная подложка: полупрозрачный белый, тонкая светлая рамка. */
fun Modifier.glass(radius: Dp = 18.dp): Modifier = this
    .clip(RoundedCornerShape(radius))
    .background(C.ghost)
    .border(1.dp, C.glassLine, RoundedCornerShape(radius))

private val noRipple = MutableInteractionSource()

fun Modifier.tap(onClick: () -> Unit): Modifier = this.clickable(interactionSource = noRipple, indication = null, onClick = onClick)

/** Тихая строка: появляется, когда системе что-то нужно. */
@Composable
fun QuietLine(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Text(
        "$text  →",
        style = T.dim.copy(color = C.text),
        modifier = modifier.clip(RoundedCornerShape(50)).background(Color.Black.copy(alpha = 0.22f)).tap(onClick).padding(vertical = 8.dp, horizontal = 16.dp),
    )
}

/** Главная кнопка: акцентная пилюля внизу под пальцем. */
@Composable
fun AccentButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Box(
        modifier
            .width(240.dp)
            .height(56.dp)
            .shadow(if (enabled) 18.dp else 0.dp, RoundedCornerShape(28.dp), ambientColor = C.glow, spotColor = C.glow)
            .clip(RoundedCornerShape(28.dp))
            .background(if (enabled) Color.White else C.faint)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, style = T.body.copy(color = if (enabled) C.ink else C.dim, fontWeight = FontWeight(600)))
    }
}

/** Цифра, которая плавно перетекает к новому значению. */
@Composable
fun FlowingNumber(value: Int, style: TextStyle = T.huge, format: (Int) -> String = { app.ritm.engine.formatInt(it) }) {
    val shown by animateIntAsState(value, tween(700), label = "number")
    Text(format(shown), style = style)
}

fun fmt(v: Double, decimals: Int): String =
    if (decimals == 0) v.roundToInt().toString() else String.format(Locale("ru"), "%.${decimals}f", v)

/**
 * Цифра-колесо: крутишь пальцем вверх-вниз, лёгкий отклик на каждом шаге.
 * Касание — ввод с клавиатуры.
 */
@Composable
fun WheelNumber(
    value: Double,
    onChange: (Double) -> Unit,
    step: Double,
    unit: String,
    modifier: Modifier = Modifier,
    decimals: Int = 0,
    min: Double = 0.0,
    max: Double = 100_000.0,
    style: TextStyle = T.big,
    stepDp: Dp = 22.dp,
) {
    val haptic = LocalHapticFeedback.current
    val stepPx = with(LocalDensity.current) { stepDp.toPx() }
    val current by rememberUpdatedState(value)
    var acc by remember { mutableFloatStateOf(0f) }
    var editing by remember { mutableStateOf(false) }
    val drag = rememberDraggableState { d ->
        acc += d
        var v = current
        var changed = false
        while (acc <= -stepPx) { acc += stepPx; v = (v + step).coerceAtMost(max); changed = true }
        while (acc >= stepPx) { acc -= stepPx; v = (v - step).coerceAtLeast(min); changed = true }
        if (changed && v != current) {
            haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove)
            onChange(Math.round(v / step) * step)
        }
    }
    Column(
        modifier.draggable(drag, Orientation.Vertical, onDragStopped = { acc = 0f }).padding(horizontal = 24.dp, vertical = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        if (editing) {
            // Ввод с клавиатуры: поле шириной с число (не выталкивает соседей), старое значение выделено —
            // новое печатается поверх; сохраняется и по «Готово», и когда уходишь с поля.
            val initial = remember { fmt(value, decimals).replace('.', ',') }
            var tf by remember { mutableStateOf(TextFieldValue(initial, TextRange(0, initial.length))) }
            val fr = remember { FocusRequester() }
            var focused by remember { mutableStateOf(false) }
            val commitChange by rememberUpdatedState(onChange)
            fun commit() {
                if (!editing) return
                tf.text.replace(',', '.').toDoubleOrNull()?.let { commitChange(it.coerceIn(min, max)) }
                editing = false
            }
            BasicTextField(
                value = tf,
                onValueChange = { v ->
                    val clean = v.text.filter { c -> c.isDigit() || ((c == ',' || c == '.') && decimals > 0) }.take(7)
                    tf = if (clean == v.text) v else TextFieldValue(clean, TextRange(clean.length))
                    // Значение уходит наверх сразу при наборе — ничего не теряется, даже если сразу выйти с экрана.
                    clean.replace(',', '.').toDoubleOrNull()?.takeIf { it in min..max }?.let { commitChange(it) }
                },
                textStyle = style.copy(textAlign = TextAlign.Center, color = C.accent),
                cursorBrush = SolidColor(Color.White),
                singleLine = true,
                keyboardOptions = KeyboardOptions(
                    keyboardType = if (decimals > 0) KeyboardType.Decimal else KeyboardType.Number,
                    imeAction = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(onDone = { commit() }),
                modifier = Modifier
                    .width(IntrinsicSize.Min)
                    .widthIn(min = 40.dp, max = 200.dp)
                    .focusRequester(fr)
                    .onFocusChanged { f -> if (f.isFocused) focused = true else if (focused) commit() },
            )
            LaunchedEffect(Unit) { fr.requestFocus() }
        } else {
            Text(fmt(value, decimals).replace('.', ','), style = style, modifier = Modifier.tap { editing = true })
        }
        Text(unit, style = T.dim)
    }
}

/** Кнопка в кольце: кольцо заполняется во время отдыха. */
@Composable
fun RingButton(label: String, progress: Float?, onClick: () -> Unit, size: Dp = 140.dp) {
    Box(Modifier.size(size).clip(CircleShape).background(C.ghost).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(size)) {
            val stroke = 3.dp.toPx()
            val inset = stroke
            val arcSize = androidx.compose.ui.geometry.Size(this.size.width - inset * 2, this.size.height - inset * 2)
            val tl = Offset(inset, inset)
            drawArc(C.glassLine, 0f, 360f, false, tl, arcSize, style = Stroke(stroke))
            drawArc(Color.White, -90f, 360f * (progress ?: 1f), false, tl, arcSize, style = Stroke(stroke, cap = StrokeCap.Round))
        }
        Text(label, style = T.title)
    }
}

/** Кольцо дня вокруг главной цифры: сколько уже съедено из плана. */
@Composable
fun DayRing(progress: Float, over: Boolean, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val p by animateFloatAsState(progress.coerceIn(0f, 1f), tween(900), label = "ring")
    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.matchParentSize()) {
            val stroke = 6.dp.toPx()
            val tl = Offset(stroke, stroke)
            val sz = androidx.compose.ui.geometry.Size(size.width - stroke * 2, size.height - stroke * 2)
            drawArc(Color.White.copy(alpha = 0.14f), 0f, 360f, false, tl, sz, style = Stroke(stroke))
            val brush = if (over) Brush.sweepGradient(listOf(C.over, C.over)) else
                Brush.sweepGradient(listOf(Color.White.copy(alpha = 0.35f), Color.White, Color.White.copy(alpha = 0.35f)))
            rotate(-90f) { drawArc(brush, 0f, 360f * p, false, tl, sz, style = Stroke(stroke, cap = StrokeCap.Round)) }
        }
        content()
    }
}

@Composable
fun Dots(count: Int) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        repeat(count) { Box(Modifier.size(10.dp).shadow(8.dp, CircleShape, ambientColor = C.glow, spotColor = C.glow).clip(CircleShape).background(Color.White)) }
    }
}

/** Небольшой выбор «чипами» — без рамок, выбранный подсвечен акцентом. */
@Composable
fun <T> Chips(options: List<Pair<T, String>>, selected: T?, onSelect: (T) -> Unit, modifier: Modifier = Modifier) {
    Row(modifier, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (v, label) ->
            val on = v == selected
            Text(
                label,
                style = T.body.copy(color = if (on) C.ink else C.text),
                modifier = Modifier
                    .clip(RoundedCornerShape(20.dp))
                    .background(if (on) Color.White else C.ghost)
                    .clickable { onSelect(v) }
                    .padding(horizontal = 14.dp, vertical = 8.dp),
            )
        }
    }
}

/** Отмена последнего действия: снизу, 4 секунды. */
class UndoState {
    var message by mutableStateOf<String?>(null)
        private set
    var action: (() -> Unit)? = null
        private set
    private var serial by mutableStateOf(0)
    val key: Int get() = serial

    fun show(message: String, undo: () -> Unit) {
        this.message = message; action = undo; serial++
    }

    fun hide() { message = null; action = null }
}

@Composable
fun UndoBar(state: UndoState, modifier: Modifier = Modifier) {
    val msg = state.message ?: return
    LaunchedEffect(state.key) {
        kotlinx.coroutines.delay(4_000)
        state.hide()
    }
    val alpha = remember(state.key) { Animatable(0f) }
    LaunchedEffect(state.key) { alpha.animateTo(1f, tween(200)) }
    Row(
        modifier
            .padding(horizontal = 16.dp)
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Color.Black.copy(alpha = 0.55f * alpha.value))
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(msg, style = T.body, modifier = Modifier.weight(1f))
        Text("Отменить", style = T.body.copy(color = C.accent), modifier = Modifier.tap { state.action?.invoke(); state.hide() })
    }
}

@Composable
fun Gap(h: Dp) = Spacer(Modifier.height(h))

/** Текущий стек экранов и экран, который сейчас рисуется (для автосохранения). */
val LocalNav = androidx.compose.runtime.staticCompositionLocalOf<Nav?> { null }
val LocalRoute = androidx.compose.runtime.staticCompositionLocalOf<Route?> { null }

/**
 * Автосохранение. save срабатывает, когда экран закрыт (назад, другая вкладка) или приложение
 * свёрнуто / экран погас. Переход вглубь (на экран поверх этого) ничего не сохраняет.
 * Вызовы идут по очереди, поэтому save может пометить «уже сохранено» и не задвоить запись.
 */
@Composable
fun OnLeave(save: suspend () -> Unit) {
    val app = androidx.compose.ui.platform.LocalContext.current.app
    val nav = LocalNav.current
    val route = LocalRoute.current
    val latest by rememberUpdatedState(save)
    val lock = remember { kotlinx.coroutines.sync.Mutex() }
    val lifecycle = androidx.lifecycle.compose.LocalLifecycleOwner.current.lifecycle
    androidx.compose.runtime.DisposableEffect(lifecycle) {
        fun run() { app.scope.launch { lock.lock(); try { latest() } finally { lock.unlock() } } }
        val obs = androidx.lifecycle.LifecycleEventObserver { _, e -> if (e == androidx.lifecycle.Lifecycle.Event.ON_STOP) run() }
        lifecycle.addObserver(obs)
        onDispose {
            lifecycle.removeObserver(obs)
            val pushedOver = nav != null && route != null && nav.stack.any { it === route }
            if (!pushedOver) run()
        }
    }
}
