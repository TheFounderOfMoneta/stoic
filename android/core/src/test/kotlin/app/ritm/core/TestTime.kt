package app.ritm.core

import java.time.LocalDate
import java.time.ZoneId

val MSK: ZoneId = ZoneId.of("Europe/Moscow")
val BASE: LocalDate = LocalDate.of(2026, 10, 5) // понедельник

/** Момент: день от BASE, часы, минуты по Москве. */
fun t(day: Int, h: Int, m: Int = 0, zone: ZoneId = MSK): Long =
    BASE.plusDays(day.toLong()).atTime(h, m).atZone(zone).toInstant().toEpochMilli()

/** Пинги активности каждую минуту на [from, to]. */
fun pings(from: Long, to: Long, stepMs: Long = 60_000): List<Long> = (from..to step stepMs).toList()
