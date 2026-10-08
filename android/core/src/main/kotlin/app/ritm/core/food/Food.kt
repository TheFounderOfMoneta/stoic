package app.ritm.core.food

import app.ritm.core.time.DAY
import app.ritm.core.time.HOUR
import app.ritm.core.time.MINUTE
import kotlin.math.exp
import kotlin.math.pow

/** Запись еды для подсказок: что, когда и сколько минут прошло от пробуждения. */
data class FoodHit(val itemKey: String, val at: Long, val minutesSinceWake: Int)

/**
 * «Обычно сейчас»: чем ближе время от пробуждения к прошлым приёмам этого продукта и чем свежее — тем выше.
 */
fun suggestFood(history: List<FoodHit>, now: Long, minutesSinceWake: Int, limit: Int = 12): List<String> {
    val recent = history.filter { now - it.at <= 30 * DAY }
    val score = HashMap<String, Double>()
    for (h in recent) {
        val daysAgo = (now - h.at).toDouble() / DAY
        val recency = 0.97.pow(daysAgo)
        val dt = (h.minutesSinceWake - minutesSinceWake) / 90.0
        val timeFit = exp(-dt * dt)
        score.merge(h.itemKey, recency * (0.15 + timeFit), Double::plus)
    }
    return score.entries.sortedByDescending { it.value }.take(limit).map { it.key }
}

/** Одинаковый набор продуктов в одном приёме 3+ раз за 30 дней → предложить комплект. */
fun suggestCombos(history: List<FoodHit>, now: Long, existing: Set<Set<String>>, mealGapMs: Long = 20 * MINUTE): List<Set<String>> {
    val meals = mutableListOf<MutableSet<String>>()
    var mealStart = Long.MIN_VALUE
    for (h in history.filter { now - it.at <= 30 * DAY }.sortedBy { it.at }) {
        if (meals.isEmpty() || h.at - mealStart > mealGapMs) { meals += mutableSetOf(h.itemKey); mealStart = h.at }
        else meals.last() += h.itemKey
    }
    return meals.filter { it.size >= 2 }.groupingBy { it.toSet() }.eachCount()
        .filter { (set, n) -> n >= 3 && set !in existing }.keys.toList()
}

/**
 * Одно напоминание о еде в день — по своему же шаблону:
 * обычно первая запись через N часов после пробуждения; если прошло N + 1 ч и записей нет — напомнить.
 */
fun shouldRemindFood(
    now: Long,
    wakeAt: Long,
    firstMealOffsetsMin: List<Int>,
    loggedToday: Boolean,
    skipToday: Boolean,
    remindedToday: Boolean,
): Boolean {
    if (loggedToday || skipToday || remindedToday) return false
    if (firstMealOffsetsMin.size < 5) return false
    val typical = firstMealOffsetsMin.sorted()[firstMealOffsetsMin.size / 2]
    return now - wakeAt >= typical * MINUTE + HOUR
}

/** Карточка веса живёт 4 часа после пробуждения, пока вес не записан. */
fun weightCardVisible(now: Long, wakeAt: Long?, weighedToday: Boolean, dismissed: Boolean): Boolean =
    wakeAt != null && !weighedToday && !dismissed && now >= wakeAt && now - wakeAt <= 4 * HOUR
