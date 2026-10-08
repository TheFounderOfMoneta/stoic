package app.ritm.ui

import androidx.compose.animation.core.Animatable
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

/** Палитра: чёрный (AMOLED), белый, серый, один акцент. */
object C {
    val bg = Color(0xFF000000)
    val text = Color(0xFFF2F2F2)
    val dim = Color(0xFF8E8E93)
    val faint = Color(0xFF2C2C2E)
    /** Подсказки в пустых полях: заметно, но тише текста. */
    val hint = Color(0xFF5A5A5F)
    val ghost = Color(0xFF1A1A1C)
    val accent = Color(0xFF8FE3B8)
}

/** Один шрифт, четыре размера. Цифры моноширинные — не прыгают при смене. */
object T {
    private val tnum = "tnum"
    val huge = TextStyle(fontSize = 96.sp, fontWeight = FontWeight.Thin, fontFeatureSettings = tnum, color = C.text, letterSpacing = (-2).sp)
    val big = TextStyle(fontSize = 64.sp, fontWeight = FontWeight.Thin, fontFeatureSettings = tnum, color = C.text)
    val title = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.Normal, color = C.text)
    val body = TextStyle(fontSize = 17.sp, fontWeight = FontWeight.Normal, color = C.text)
    val dim = TextStyle(fontSize = 14.sp, fontWeight = FontWeight.Normal, color = C.dim)
}

@Composable
fun RitmTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = C.accent, onPrimary = Color.Black, background = C.bg, onBackground = C.text,
            surface = C.bg, onSurface = C.text, surfaceVariant = C.ghost, onSurfaceVariant = C.dim,
            surfaceContainer = C.ghost, surfaceContainerHigh = C.ghost, surfaceContainerLow = C.bg,
            outline = C.faint, secondary = C.dim,
        ),
        content = content,
    )
}

private val noRipple = MutableInteractionSource()

fun Modifier.tap(onClick: () -> Unit): Modifier = this.clickable(interactionSource = noRipple, indication = null, onClick = onClick)

/** Тихая строка: появляется, когда системе что-то нужно. */
@Composable
fun QuietLine(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Text("$text  →", style = T.body.copy(color = C.dim), modifier = modifier.tap(onClick).padding(vertical = 12.dp, horizontal = 16.dp))
}

/** Главная кнопка: акцентная пилюля внизу под пальцем. */
@Composable
fun AccentButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Box(
        modifier
            .width(240.dp)
            .height(56.dp)
            .clip(RoundedCornerShape(28.dp))
            .background(if (enabled) C.accent else C.faint)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, style = T.body.copy(color = if (enabled) Color.Black else C.dim, fontWeight = FontWeight.Medium))
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
            var text by remember { mutableStateOf(fmt(value, decimals).replace('.', ',')) }
            val fr = remember { FocusRequester() }
            BasicTextField(
                value = text,
                onValueChange = { text = it.filter { c -> c.isDigit() || c == ',' || c == '.' }.take(7) },
                textStyle = style.copy(textAlign = TextAlign.Center, color = C.accent),
                cursorBrush = SolidColor(C.accent),
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = {
                    text.replace(',', '.').toDoubleOrNull()?.let { onChange(it.coerceIn(min, max)) }
                    editing = false
                }),
                modifier = Modifier.width(220.dp).focusRequester(fr),
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
fun RingButton(label: String, progress: Float?, onClick: () -> Unit, size: Dp = 132.dp) {
    Box(Modifier.size(size).clip(CircleShape).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(size)) {
            val stroke = 3.dp.toPx()
            drawArc(C.faint, 0f, 360f, false, style = Stroke(stroke))
            if (progress != null) drawArc(C.accent, -90f, 360f * progress, false, style = Stroke(stroke, cap = StrokeCap.Round))
            else drawArc(C.accent, 0f, 360f, false, style = Stroke(stroke))
        }
        Text(label, style = T.body)
    }
}

@Composable
fun Dots(count: Int) {
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        repeat(count) { Box(Modifier.size(10.dp).clip(CircleShape).background(C.accent)) }
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
                style = T.body.copy(color = if (on) Color.Black else C.dim),
                modifier = Modifier
                    .clip(RoundedCornerShape(20.dp))
                    .background(if (on) C.accent else C.ghost)
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
            .background(C.ghost.copy(alpha = alpha.value))
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(msg, style = T.body, modifier = Modifier.weight(1f))
        Text("Отменить", style = T.body.copy(color = C.accent), modifier = Modifier.tap { state.action?.invoke(); state.hide() })
    }
}

@Composable
fun Gap(h: Dp) = Spacer(Modifier.height(h))
