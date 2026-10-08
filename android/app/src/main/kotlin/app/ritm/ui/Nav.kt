package app.ritm.ui

import androidx.compose.runtime.mutableStateListOf

sealed interface Route {
    data object Home : Route
    data object Tasks : Route
    data object Notes : Route
    data object Settings : Route
    data object Plus : Route
    data object FoodPick : Route
    data class FoodAmount(val productId: Long, val editFoodId: Long? = null) : Route
    data class AddProduct(val name: String) : Route
    data object FoodToday : Route
    data object Weight : Route
    data object Workout : Route
    data object ExercisePick : Route
    data class TaskEdit(val id: Long? = null) : Route
    data class NoteEdit(val id: Long? = null) : Route
    data object Onboarding : Route
    data class SettingsPage(val page: String) : Route
}

/** Простой стек экранов. Пустой стек — выход (в быстром режиме — закрыть окно). */
class Nav(start: Route) {
    val stack = mutableStateListOf(start)
    val top: Route get() = stack.last()

    fun go(r: Route) { stack.add(r) }
    fun replace(r: Route) { stack[stack.lastIndex] = r }
    fun back(): Boolean {
        if (stack.size <= 1) return false
        stack.removeAt(stack.lastIndex)
        return true
    }
    fun home() {
        while (stack.size > 1) stack.removeAt(stack.lastIndex)
    }
}

/** Где показываются экраны: обычное приложение или быстрый ввод поверх экрана блокировки. */
interface Host {
    val quick: Boolean
    /** Ввод завершён: в приложении — на главный, в быстром режиме — закрыть. */
    fun done()
    /** Экран требует разблокировки (просмотр истории, правка). */
    fun needsUnlock(route: Route)
}
