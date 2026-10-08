package app.ritm.core.day

/** Тихая строка над цифрой: показывается одна, по приоритету. */
enum class NowItem { WORKOUT, PROBLEM, WEIGHT, PLACE, SLEEP_UNSURE }

fun nowLine(
    workoutActive: Boolean,
    hasProblem: Boolean,
    weightCard: Boolean,
    placeSuggestion: Boolean,
    sleepUnsure: Boolean,
): NowItem? = when {
    workoutActive -> NowItem.WORKOUT
    hasProblem -> NowItem.PROBLEM
    weightCard -> NowItem.WEIGHT
    placeSuggestion -> NowItem.PLACE
    sleepUnsure -> NowItem.SLEEP_UNSURE
    else -> null
}
