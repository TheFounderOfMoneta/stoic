"""События системы через D-Bus: экран заблокирован/разблокирован, компьютер засыпает/проснулся.

* Заблокированный экран: диктовку не начинаем (на экране блокировки Правый Alt нужен для
  ввода пароля), начатую — заканчиваем, а текст не вставляем в поле пароля: он ждёт разблокировки.
* Сон (logind PrepareForSleep): запись заканчиваем до сна, после пробуждения сразу чиним
  соединения, микрофон и видеокарту — не дожидаясь, пока это заметит сторож.

Нет D-Bus (другой рабочий стол, тесты) — просто ничего не приходит, приложение работает как раньше.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import SLOT, QObject, Signal, Slot

log = logging.getLogger(__name__)

SCREENSAVERS = (
    ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver"),
    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver"),
)


class SystemEvents(QObject):
    lock_changed = Signal(bool)     # True — экран заблокирован
    sleep_changed = Signal(bool)    # True — засыпаем, False — проснулись

    def __init__(self, parent=None):
        super().__init__(parent)
        self.locked = False
        self._watchers = []
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher
        except ImportError:
            return
        try:
            session = QDBusConnection.sessionBus()
            if session.isConnected():
                for service, path, iface in SCREENSAVERS:
                    session.connect(service, path, iface, "ActiveChanged", self, SLOT("_on_active(bool)"))
                # Начальное состояние — асинхронно: главный поток ответа не ждёт.
                service, path, iface = SCREENSAVERS[0]
                call = session.asyncCall(QDBusMessage.createMethodCall(service, path, iface, "GetActive"), 2000)
                watcher = QDBusPendingCallWatcher(call, self)
                watcher.finished.connect(self._got_active)
                self._watchers.append(watcher)
            system = QDBusConnection.systemBus()
            if system.isConnected():
                system.connect("org.freedesktop.login1", "/org/freedesktop/login1",
                               "org.freedesktop.login1.Manager", "PrepareForSleep", self, SLOT("_on_sleep(bool)"))
        except Exception:  # noqa: BLE001 — без событий системы приложение всё равно работает
            log.debug("D-Bus недоступен", exc_info=True)

    def _got_active(self, watcher) -> None:
        try:
            reply = watcher.reply()
            args = reply.arguments() if reply is not None else []
            if args and isinstance(args[0], bool):
                self._on_active(args[0])
        except Exception:  # noqa: BLE001
            pass
        finally:
            watcher.deleteLater()

    @Slot(bool)
    def _on_active(self, active: bool) -> None:
        active = bool(active)
        if active == self.locked:
            return
        self.locked = active
        log.info("Экран %s", "заблокирован" if active else "разблокирован")
        self.lock_changed.emit(active)

    @Slot(bool)
    def _on_sleep(self, going: bool) -> None:
        log.info("Компьютер %s", "засыпает" if going else "проснулся")
        self.sleep_changed.emit(bool(going))
