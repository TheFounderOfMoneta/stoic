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

# Поиск элемента по тексту на экране (дерево доступности) и нажатие в его центр.
dump() { adb shell uiautomator dump /sdcard/ui.xml > /dev/null 2>&1; adb shell cat /sdcard/ui.xml > /tmp/ui.xml; }
tap_text() {
  dump
  xy=$(python3 -c "
import re,sys
x=open('/tmp/ui.xml',encoding='utf-8').read()
for m in re.finditer(r'text=\"([^\"]*)\"[^>]*?bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', x):
    if m.group(1)==sys.argv[1]:
        a,b,c,d=map(int,m.groups()[1:]); print((a+c)//2,(b+d)//2); break
" "$1")
  if [ -z "$xy" ]; then echo "NOT FOUND on screen: $1"; return 1; fi
  adb shell input tap $xy; sleep 1.5
}
has_text() { dump; grep -q "text=\"$1\"" /tmp/ui.xml; }
expect() { for t in "$@"; do if ! has_text "$t"; then echo "UI CHECK FAILED: нет «$t» на экране"; UI_FAIL=1; fi; done; }
UI_FAIL=0

# 1. Первый запуск — мастер настройки, ввод цифр с клавиатуры (ловим «пропадающие» поля).
adb shell am start -n $PKG/.ui.MainActivity > /dev/null
shot 00-onboarding 5
tap_text "Дальше"
shot 00b-body 2
tap_text "Мужчина"
tap_text "75,0" && { adb shell input text 82.4; adb shell input keyevent 66; sleep 1.5; }
expect "82,4" "175" "1995" "Мужчина"
shot 00c-body-weight-typed 1
tap_text "175" && { adb shell input text 181; adb shell input keyevent 66; sleep 1.5; }
expect "181" "82,4" "1995"
tap_text "1995" && { adb shell input text 1991; adb shell input keyevent 66; sleep 1.5; }
expect "181" "82,4" "1991"
shot 00d-body-all-typed 1
tap_text "Дальше"
shot 00e-access 2
tap_text "Дальше"
shot 00f-button 2
tap_text "Сделаю позже"
shot 00g-home-after-onboarding 3
expect "Сегодня" "Задачи" "Заметки" "Добавить"
# Автосохранение: открыть «Тело», поменять вес с клавиатуры и сразу выйти назад — вес должен остаться.
adb shell am start -n $PKG/.ui.MainActivity --es route page:body > /dev/null; sleep 3
tap_text "82,4" && { adb shell input text 83.1; sleep 0.5; }
adb shell input keyevent KEYCODE_BACK; sleep 0.5; adb shell input keyevent KEYCODE_BACK; sleep 1.5
adb shell am start -n $PKG/.ui.MainActivity --es route page:body > /dev/null; sleep 3
expect "83,1"
shot 00h-body-autosaved 1
adb shell input keyevent KEYCODE_BACK; sleep 1

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

# 3б. Тренировка с нуля, как человек: выбрать упражнение из списка, своё — через «Добавить».
adb shell am start -n $PKG/.ui.MainActivity --es route workout > /dev/null; sleep 3
expect "Выбрать упражнение"
shot 13b-workout-empty 1
tap_text "Выбрать упражнение"
expect "Жим лёжа" "Подтягивания"
shot 13c-exercise-list 1
tap_text "Подтягивания"
expect "Подтягивания" "Подход" "сделали подход — нажмите"
tap_text "Подход"
expect "подходов: 1"
shot 13d-workout-first-set 1
tap_text "сменить упражнение"
tap_text "Найти или ввести своё" && { adb shell input text "Face%spull"; sleep 1; }
expect "+ Добавить «Face pull»"
shot 13e-exercise-custom 1
tap_text "+ Добавить «Face pull»"
expect "Face pull" "Подход"
tap_text "Завершить"
sleep 2

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
if [ "$UI_FAIL" = "1" ]; then echo "UI CHECKS FAILED"; exit 1; fi
if grep -E "FATAL EXCEPTION|Process: $PKG|ANR in $PKG" shots/logcat.txt; then
  grep -B 5 -A 40 -E "FATAL EXCEPTION|ANR in" shots/logcat.txt | head -160
  echo "CRASH FOUND"; exit 1
fi
echo "NO CRASHES"
