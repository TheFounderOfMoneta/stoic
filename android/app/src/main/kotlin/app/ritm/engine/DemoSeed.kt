package app.ritm.engine

import android.content.Context
import android.content.pm.ApplicationInfo
import app.ritm.app
import app.ritm.core.energy.GoalType
import app.ritm.core.energy.Sex
import java.time.LocalDate

/**
 * Демо-данные для прогона в эмуляторе (скриншоты, проверка падений).
 * Работает только в отладочной сборке и только до первого запуска — настоящие данные не трогает.
 */
object DemoSeed {
    suspend fun run(context: Context, kind: String) {
        val debuggable = context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
        val repo = context.app.repo
        if (!debuggable) return
        when (kind) {
            "seed" -> {
                if (repo.settings.get().onboarded) return
                repo.settings.setGoals("Похудеть до 78 кг, тренироваться 3 раза в неделю")
                repo.settings.setBody(Sex.MALE, 181.0, LocalDate.of(1995, 7, 1))
                repo.settings.setGoal(GoalType.LOSE, 0.5)
                repo.addWeight(82.4, System.currentTimeMillis() - 3 * 3600_000L)
                repo.seedProducts()
                listOf("Овсянка на молоке" to 250.0, "Банан" to 120.0, "Кофе чёрный" to 200.0, "Гречка варёная" to 200.0, "Куриная грудка варёная" to 150.0)
                    .forEachIndexed { i, (name, g) ->
                        repo.searchProducts(name).firstOrNull { it.name == name }?.let { repo.addFood(it, g, System.currentTimeMillis() - (5 - i) * 1800_000L) }
                    }
                val today = LocalDate.now()
                repo.addTask("Позвонить в банк", today, "15:00")
                repo.addTask("Купить молоко", today, null)
                repo.addTask("Записаться к врачу", today.plusDays(1), null)
                repo.addTask("Найти тренера", null, null)
                repo.addTask("Отпуск — забронировать", today.plusDays(9), null)
                val n = repo.addNote("Идея: вечером не открывать ленту — читать 20 минут вместо этого")
                repo.publishNote(n.id)
                repo.addMoment()
                repo.settings.setOnboarded(true)
            }
            "workout" -> {
                repo.startWorkout(auto = false)
                repo.chooseExercise("Жим лёжа")
                repo.addSet(80.0, 8)
                repo.addSet(80.0, 8)
                repo.chooseExercise("Тяга верхнего блока")
                repo.addSet(55.0, 12)
                WorkoutClock.startRest(context, 90)
            }
        }
        context.app.day.refresh()
    }
}
