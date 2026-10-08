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

# 5. Кнопка Bixby через системный журнал. В эмуляторе нет Samsung, поэтому пишем в журнал
#    ровно такие строки, как S10+ (тег PhoneWindowManagerExt), с реальными интервалами нажатий.
adb shell pm grant $PKG android.permission.READ_LOGS
adb shell am force-stop $PKG; sleep 1
adb shell am start -n $PKG/.ui.MainActivity > /dev/null; sleep 4
L='getIntentBixbyService, keyPressType=-1 interactive=true isUnlockFP=false longPress=false doublePress=false isPowerKeyCombination=false'
bx() { echo "log -p d -t PhoneWindowManagerExt '$L'"; }
adb shell input keyevent KEYCODE_HOME; sleep 1
# два нажатия — еда (нажал/отпустил 0,14 с, пауза 0,2 с)
adb shell "$(bx); sleep 0.14; $(bx); sleep 0.2; $(bx); sleep 0.14; $(bx)"
shot 16-bixby-double-food 3
adb shell input keyevent KEYCODE_BACK; sleep 1
# три нажатия — отметка момента
adb shell "$(bx); sleep 0.12; $(bx); sleep 0.18; $(bx); sleep 0.12; $(bx); sleep 0.18; $(bx); sleep 0.12; $(bx)"
sleep 2
# удержание 1,5 с — голосовая заметка
adb shell "$(bx); sleep 1.5; $(bx)"
sleep 3
route notes 17-notes-after-bixby
route page:button 17b-button-page

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
