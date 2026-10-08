#!/usr/bin/env bash
# Прогон в эмуляторе Android 12: все экраны, кнопка Bixby, скриншоты, проверка падений.
set -u
APK=$(ls apk/*.apk | head -1)
PKG=app.ritm
mkdir -p shots
adb install -r "$APK"
adb logcat -c

shot() { sleep "${2:-3}"; adb exec-out screencap -p > "shots/$1.png"; echo "shot $1"; }
route() { adb shell am start -n $PKG/.ui.MainActivity --es route "$1" > /dev/null; shot "$2"; adb shell input keyevent KEYCODE_BACK; sleep 1; }

# 1. Первый запуск — мастер настройки.
adb shell am start -n $PKG/.ui.MainActivity > /dev/null
shot 00-onboarding 5

# Доступы, которые можно выдать с компьютера (как сделает человек кнопками).
for p in ACCESS_FINE_LOCATION ACCESS_COARSE_LOCATION ACCESS_BACKGROUND_LOCATION ACTIVITY_RECOGNITION RECORD_AUDIO; do
  adb shell pm grant $PKG android.permission.$p || true
done
adb shell appops set $PKG GET_USAGE_STATS allow || true
adb shell dumpsys deviceidle whitelist +$PKG > /dev/null || true

# 2. Демо-данные и главный экран.
adb shell am start -n $PKG/.ui.MainActivity --es demo seed > /dev/null
sleep 6
adb shell input keyevent KEYCODE_BACK; sleep 1
adb shell am start -n $PKG/.ui.MainActivity > /dev/null
shot 01-home 5

# 3. Все экраны.
route tasks 02-tasks
route notes 03-notes
route food-today 04-food-today
route plus 05-plus
route food 06-food-pick
route weight 07-weight
route settings 08-settings
route page:body 09-body
route page:places 10-places
route page:button 11-button
route page:advanced 12-advanced
route page:goals 13-goals

# 4. Тренировка с подходами и отдыхом.
adb shell am start -n $PKG/.ui.MainActivity --es demo workout > /dev/null
sleep 4
route workout 14-workout
shot 15-home-during-workout 2

# 5. Кнопка Bixby через службу спецвозможностей. В эмуляторе код Bixby (1082) недопустим,
#    поэтому тестовая сборка слушает ещё и F12 — логика жестов та же.
adb shell settings put secure enabled_accessibility_services $PKG/$PKG.button.ButtonService
adb shell settings put secure accessibility_enabled 1
sleep 5
adb shell dumpsys accessibility | grep -i "ritm" | head -5
adb shell am start -n $PKG/.ui.MainActivity > /dev/null; sleep 2
adb shell input keyevent KEYCODE_F12 KEYCODE_F12 KEYCODE_F12   # три — отметка момента
sleep 2
adb shell input keyevent KEYCODE_F12 KEYCODE_F12               # два — еда (быстрый ввод)
shot 16-bixby-double-food 3
adb shell input keyevent KEYCODE_BACK; sleep 1
adb shell input keyevent KEYCODE_F12                           # одно — «+» (или подход на тренировке)
shot 16b-bixby-single 3
adb shell input keyevent KEYCODE_BACK; sleep 1
adb shell input keyevent --longpress KEYCODE_F12               # удержание — голосовая заметка
sleep 3
route notes 17-notes-after-bixby

# 6. Экран блокировки: два нажатия при выключенном экране — еда поверх блокировки.
adb shell input keyevent KEYCODE_SLEEP; sleep 3
adb shell input keyevent KEYCODE_F12 KEYCODE_F12
shot 18-locked-food 4
adb shell input keyevent KEYCODE_BACK; sleep 1

# 7. Шторка.
adb shell input keyevent KEYCODE_WAKEUP; sleep 1
adb shell cmd statusbar expand-notifications; shot 19-notification 2
adb shell cmd statusbar collapse

# 8. Падения.
adb logcat -d > shots/logcat.txt
adb shell dumpsys activity services $PKG > shots/services.txt
adb shell run-as $PKG ls -la databases files > shots/files.txt 2>&1 || true
if grep -E "FATAL EXCEPTION|Process: $PKG|ANR in $PKG" shots/logcat.txt; then
  grep -B 5 -A 40 -E "FATAL EXCEPTION|ANR in" shots/logcat.txt | head -160
  echo "CRASH FOUND"; exit 1
fi
echo "NO CRASHES"
