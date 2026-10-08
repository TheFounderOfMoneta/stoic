package app.ritm.core.tasks

import java.time.LocalDate

data class TaskItem(val id: Long, val date: LocalDate?, val done: Boolean, val carryCount: Int = 0)

/** Невыполненное с прошлых дней переносится на сегодня, счётчик переносов растёт. */
fun carryOver(tasks: List<TaskItem>, today: LocalDate): List<TaskItem> = tasks.map { t ->
    if (!t.done && t.date != null && t.date.isBefore(today)) t.copy(date = today, carryCount = t.carryCount + 1) else t
}
