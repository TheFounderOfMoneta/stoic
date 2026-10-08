# Ритм — телефон

Android-приложение персональной системы данных. Телефон — для ввода, ПК и ИИ — для анализа.

## Установка
1. GitHub → Actions → «Ритм — Android» → последний зелёный запуск → артефакт `ritm-apk` → `app-debug.apk`.
2. Установить на телефон (разрешить установку из этого источника).
3. Первый запуск проведёт по шагам: цели → о себе → доступы → кнопка слева.
4. Кнопка Bixby — один раз с компьютера: `adb shell pm grant app.ritm android.permission.READ_LOGS`.
   На S10 система не отдаёт кнопку спецвозможностям, но пишет каждое нажатие и отпускание в системный журнал;
   Ритм читает только эти строки (тег `PhoneWindowManagerExt`). На Android 12 доступ переживает перезагрузку.
   Затем включить «Ритм — сон по касаниям» в Спецвозможностях (сигнал для определения сна).
5. Samsung: Обслуживание устройства → Батарея → Ограничения в фоне → убрать Ритм из «спящих».

Все сборки подписаны одним ключом (`app/ritm-debug.keystore`), поэтому обновления ставятся поверх без потери данных.

## Структура
- `core/` — чистая логика без Android, с тестами сценариев (`./gradlew :core:test` работает и без Android SDK):
  сон, границы дня, калории и калибровка, фильтр подмены GPS, стоянки и места, отпечатки Wi-Fi/вышек, жесты кнопки, подсказки еды, перенос задач.
- `app/` — Android: данные (Room), фоновый сбор, кнопка Bixby, интерфейс (Compose).

## Протокол отправки
`POST {адрес}/v1/events`, заголовок `Authorization: Bearer {ключ}`, тело:
```json
{
  "device": "phone",
  "sentAt": 1760000000000,
  "events": [
    {"id": "uuid", "type": "food.add", "at": 1760000000000, "offsetSec": 10800, "payload": {"name": "Гречка варёная", "grams": 200, "kcal": 220}}
  ]
}
```
- `at` — UTC в мс, `offsetSec` — смещение пояса в момент события. `id` уникален: повторная отправка не должна создавать дублей.
- Ответ 2xx — пачка считается доставленной. Иначе повтор позже. Без интернета всё копится на телефоне.
- Раз в час приходит `heartbeat` (заряд, очередь, каких доступов нет). Сторож на сервере: 3 пропущенных heartbeat → тревога.

Типы событий: `food.add/update/delete`, `product.add`, `weight.add/delete`, `workout.start/end/discard`, `set.add/update/delete`,
`task.add/update/done/undone/delete/carry`, `note.save`, `note.voice` (аудио m4a в base64), `moment`, `steps`, `ping`,
`span.open/close` (charging, dark, screen, still, walking, vehicle, place:<id>), `fix` (точка + вердикт фильтра), `stay`,
`geofence`, `place.add/update/delete/dismiss`, `usage` (сессии приложений), `sleep.mark`, `day.start`, `day.summary`,
`day.flag`, `calibration`, `profile.goals`, `profile.body`, `heartbeat`.
