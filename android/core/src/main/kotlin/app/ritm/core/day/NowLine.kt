package app.ritm.core.day

/**
 * Тихая строка над цифрой: показывается одна, по приоритету.
 * Вес утром важнее напоминаний о доступах — иначе невыполненная настройка прятала бы его весь день.
 */
enum class NowItem { WORKOUT, PROBLEM, WEIGHT, PLACE, SLEEP_UNSURE }

fun nowLine(
    workoutActive: Boolean,
    hasProblem: Boolean,
    weightCard: Boolean,
    placeSuggestion: Boolean,
    sleepUnsure: Boolean,
): NowItem? = when {
    workoutActive -> NowItem.WORKOUT
    weightCard -> NowItem.WEIGHT
    hasProblem -> NowItem.PROBLEM
    placeSuggestion -> NowItem.PLACE
    sleepUnsure -> NowItem.SLEEP_UNSURE
    else -> null
}
