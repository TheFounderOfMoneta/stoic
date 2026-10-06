"""Захват микрофона (PipeWire через PortAudio) и звуковые сигналы."""
from __future__ import annotations

import logging
import math
import os
import queue
import re
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

from . import pwaudio

log = logging.getLogger(__name__)

SR = 16000
BLOCK = 512  # 32 мс — совпадает с кадром Silero VAD
CLIP_LEVEL = 0.985  # |x| выше — сэмпл «упёрся в потолок» (перегруз)

# PortAudio (sounddevice) загружается в фоне: при загрузке он опрашивает звуковую систему,
# и если PipeWire завис, зависнет только фоновый поток, а не облачко и не весь запуск.
_sd_module = None
_sd_error: Optional[BaseException] = None
_sd_ready = threading.Event()
_sd_lock = threading.Lock()
# PortAudio не потокобезопасен: потоки открываем и закрываем строго по одному.
PA_LOCK = threading.RLock()


def _load_sd() -> None:
    global _sd_module, _sd_error
    with _sd_lock:
        if _sd_ready.is_set():
            return
        try:
            import sounddevice
            _sd_module = sounddevice
        except (ImportError, OSError) as exc:  # нет libportaudio2
            _sd_error = exc
            log.error("%s", _backend_text())
        finally:
            _sd_ready.set()


def preload() -> None:
    """Начать загрузку PortAudio в фоне (при запуске приложения)."""
    if not _sd_ready.is_set():
        threading.Thread(target=_load_sd, name="portaudio-init", daemon=True).start()


def get_sd(wait: bool = True):
    """Модуль sounddevice или None. В главном потоке не ждём (wait=False): если PortAudio
    ещё не загрузился — считаем, что его пока нет."""
    if not _sd_ready.is_set():
        if not wait:
            return None
        _load_sd()
    return _sd_module


def _backend_text() -> str:
    return f"Не найден PortAudio ({_sd_error}). Установите пакет: sudo apt install libportaudio2"


def audio_backend_error(wait: bool = True) -> Optional[str]:
    if get_sd(wait) is None and _sd_ready.is_set():
        return _backend_text()
    return None


class AudioHang(RuntimeError):
    """Звуковая система не отвечает (PipeWire/PortAudio завис)."""


_HW_SUFFIX = re.compile(r"\s*\((hw|plughw):\d+,\d+\)\s*$")


def _pretty_portaudio_name(name: str) -> str:
    """«HD-Audio Generic: ALC255 Analog (hw:1,0)» → «HD-Audio Generic: ALC255 Analog»."""
    return _HW_SUFFIX.sub("", name).strip()


def list_input_devices() -> list[tuple[Optional[str], str]]:
    """[(значение для настроек, понятное имя)]. None — системный микрофон по умолчанию.

    Микрофоны берём из PipeWire (там человеческие названия: «Встроенный микрофон»,
    «Гарнитура AirPods»…). Без PipeWire — список PortAudio без служебных устройств.
    """
    sources = pwaudio.list_sources()
    default = next((s for s in sources if s.default), None)
    label = "Как в системе"
    if default is not None:
        label += f" — {default.description}"
    out: list[tuple[Optional[str], str]] = [(None, label)]
    if sources:
        for src in sources:
            out.append(("pw:" + src.name, src.description))
        return out
    sd = get_sd(wait=False)
    if sd is None:
        return out
    try:
        seen = set()
        for dev in sd.query_devices():
            if dev.get("max_input_channels", 0) <= 0:
                continue
            name = dev["name"]
            if name in ("default", "sysdefault", "pipewire", "pulse", "dmix", "dsnoop", "jack") \
                    or name.startswith(("surround", "front:", "iec958", "spdif", "hdmi")):
                continue
            if name not in seen:
                seen.add(name)
                out.append((name, _pretty_portaudio_name(name)))
    except Exception:  # noqa: BLE001
        log.exception("Не удалось получить список устройств")
    return out


def device_label(value) -> str:
    """Понятное имя выбранного в настройках микрофона."""
    for val, label in list_input_devices():
        if val == value:
            return label
    if isinstance(value, str) and value.startswith("pw:"):
        return value[3:]
    return str(value) if value else "Как в системе"


def _pipewire_portaudio_index() -> Optional[int]:
    """Устройство PortAudio, через которое ALSA ходит в PipeWire («pipewire», «pulse», «default»)."""
    sd = get_sd()
    if sd is None:
        return None
    try:
        devices = list(sd.query_devices())
    except Exception:  # noqa: BLE001
        return None
    for wanted in ("pipewire", "pulse", "default"):
        for index, dev in enumerate(devices):
            if dev["name"] == wanted and dev.get("max_input_channels", 0) > 0:
                return index
    return None


def _resolve_device(value) -> tuple[Optional[int], dict]:
    """(индекс PortAudio, переменные окружения на время открытия).

    В настройках храним «pw:<имя узла PipeWire>» (имя постоянное, индекс PortAudio — нет).
    Поток открывается через ALSA-плагин PipeWire/Pulse, а нужный микрофон выбирается
    переменными PIPEWIRE_NODE/PULSE_SOURCE.
    """
    sd = get_sd()
    if value is None or sd is None:
        return None, {}
    if isinstance(value, int):
        return value, {}
    if isinstance(value, str) and value.startswith("pw:"):
        node = value[3:]
        if pwaudio.list_sources() and not any(s.name == node for s in pwaudio.list_sources()):
            log.warning("Микрофон %s не найден — беру системный", node)
            return None, {}
        return _pipewire_portaudio_index(), {"PIPEWIRE_NODE": node, "PULSE_SOURCE": node}
    for index, dev in enumerate(sd.query_devices()):
        if dev["name"] == value and dev.get("max_input_channels", 0) > 0:
            return index, {}
    return None, {}


class _TempEnv:
    """Переменные окружения только на время открытия потока: иначе PIPEWIRE_NODE
    увёл бы в микрофон и звуки приложения."""
    _lock = threading.Lock()

    def __init__(self, env: dict):
        self.env = env
        self.saved: dict = {}

    def __enter__(self):
        self._lock.acquire()
        for key, value in self.env.items():
            self.saved[key] = os.environ.get(key)
            os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._lock.release()


def friendly_mic_error(exc: Exception) -> str:
    """Понятное объяснение ошибки PortAudio вместо «[PaErrorCode -9985]»."""
    if isinstance(exc, AudioHang):
        return "звуковая система не отвечает"
    text = str(exc).lower()
    if "portaudio" in text and ("not found" in text or "libportaudio" in text):
        return "не установлен PortAudio (sudo apt install libportaudio2)"
    if "-9985" in text or "unavailable" in text or "busy" in text:
        return "занят другой программой или отключён"
    if "-9996" in text or "no default input" in text or "invalid device" in text or "-9998" in text:
        return "не найден — подключите микрофон или выберите другой в настройках"
    if "-9997" in text or "sample rate" in text:
        return "не поддерживает нужную частоту"
    if "permission" in text or "-9999" in text:
        return "нет доступа к звуковой системе"
    return "не удалось включить"


def level_from_block(block: np.ndarray) -> float:
    """RMS → 0..1 по шкале -58…-8 дБФС."""
    rms = float(np.sqrt(np.mean(np.square(block), dtype=np.float64))) if block.size else 0.0
    db = 20.0 * math.log10(rms + 1e-9)
    return float(min(1.0, max(0.0, (db + 58.0) / 50.0)))


def clipped_count(block: np.ndarray) -> int:
    return int(np.count_nonzero(np.abs(block) >= CLIP_LEVEL))


class Resampler:
    """Потоковый пересчёт частоты в 16 кГц.

    Положение следующего выходного сэмпла хранится как целая дробь (числитель/up),
    поэтому дробная часть не теряется между блоками и длина записи не «уплывает».
    Перед прореживанием — ФНЧ (оконный sinc) с сохранением состояния между блоками.
    """

    def __init__(self, src: int, dst: int = SR, taps: int = 63):
        g = math.gcd(int(src), int(dst))
        self.src, self.dst = int(src), int(dst)
        self.up, self.down = self.dst // g, self.src // g   # выход k ↔ вход k·down/up
        self.taps: Optional[np.ndarray] = None
        if self.src > self.dst:
            fc = 0.45 * self.dst / self.src                    # срез ~7,2 кГц, доля частоты входа
            n = np.arange(taps) - (taps - 1) / 2
            h = 2 * fc * np.sinc(2 * fc * n) * np.blackman(taps)
            self.taps = (h / h.sum()).astype(np.float32)
            self._hist = np.zeros(taps - 1, np.float32)
        self._buf = np.zeros(0, np.float32)
        self._num = 0   # позиция следующего выходного сэмпла от начала _buf, в 1/up входного сэмпла

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, np.float32).reshape(-1)
        if self.taps is not None:
            x = np.concatenate([self._hist, block])
            filtered = np.convolve(x, self.taps, mode="valid").astype(np.float32)
            self._hist = x[-(self.taps.size - 1):]
        else:
            filtered = block
        data = np.concatenate([self._buf, filtered]) if self._buf.size else filtered
        if data.size < 2:
            self._buf = data
            return np.zeros(0, np.float32)
        max_num = (data.size - 1) * self.up
        n = 0 if self._num > max_num else (max_num - self._num) // self.down + 1
        if n <= 0:
            self._buf = data
            return np.zeros(0, np.float32)
        nums = self._num + np.arange(n, dtype=np.int64) * self.down
        i0 = nums // self.up
        frac = (nums - i0 * self.up).astype(np.float32) / self.up
        i1 = np.minimum(i0 + 1, data.size - 1)
        out = data[i0] * (1.0 - frac) + data[i1] * frac
        self._num += n * self.down
        drop = min(self._num // self.up, data.size)
        self._buf = data[drop:]
        self._num -= drop * self.up
        return out.astype(np.float32)


class Recorder:
    """Микрофон → блоки 16 кГц mono float32 в «приёмник» текущей сессии + уровень громкости.

    Приёмник (sink) передаётся в start() и привязан к своей сессии: блоки уходят ровно туда,
    куда нужно, включая предзапись. После stop(sink) этот приёмник больше не получит
    ни одного блока — значит, команду «закончить» распознавателю можно слать сразу.

    Открытие и закрытие потока выполняет отдельный поток управления, по очереди: главный
    поток (облачко, клавиши) никогда не ждёт звуковую систему. Если PipeWire завис,
    stuck_for() покажет, сколько длится операция, — приложение само решит, что делать.
    on_level(level) и on_result(...) вызываются из фоновых потоков.
    """

    OPEN_TIMEOUT_S = 5.0     # дольше — звуковая система «висит», новые запросы не копим

    def __init__(self, on_level: Callable[[float], None]):
        self.on_level = on_level
        self._stream = None
        self._sink_lock = threading.Lock()   # смена приёмника (короткая, без закрытия потока)
        self._sink: Optional[Callable[[np.ndarray], None]] = None
        self._wanted: Optional[Callable[[np.ndarray], None]] = None   # кого подключить (задаётся сразу)
        self._device = None
        self._native_rate = SR
        self._resampler: Optional[Resampler] = None
        self._preroll: deque = deque(maxlen=12)   # ~0,4 с перед нажатием при «тёплом» микрофоне
        self._warm = False
        self.opened_at = 0.0
        self.last_block_at = 0.0
        self._ops: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._busy_since: Optional[float] = None
        self._busy_what = ""

    # --------------------------------------------------------------- поток управления
    def _submit(self, what: str, fn: Callable[[], None]) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name="audio-control", daemon=True)
            self._worker.start()
        self._ops.put((what, fn))

    def _run(self) -> None:
        while True:
            what, fn = self._ops.get()
            self._busy_since, self._busy_what = time.monotonic(), what
            try:
                fn()
            except Exception:  # noqa: BLE001 — поток управления не должен умирать
                log.exception("Микрофон: ошибка (%s)", what)
            finally:
                self._busy_since = None
                self._ops.task_done()

    def stuck_for(self) -> float:
        """Сколько секунд выполняется текущая операция с микрофоном (0 — ничего не делается)."""
        since = self._busy_since
        return time.monotonic() - since if since is not None else 0.0

    @property
    def wedged(self) -> bool:
        return self.stuck_for() > self.OPEN_TIMEOUT_S

    def wait_idle(self, timeout: float = 3.0) -> bool:
        """Дождаться выполнения всех операций (выход из приложения, тесты)."""
        deadline = time.monotonic() + timeout
        while self._ops.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True

    # --------------------------------------------------------------- поток PortAudio
    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        self.last_block_at = time.monotonic()
        block = indata[:, 0].astype(np.float32, copy=True)
        resampler = self._resampler
        if resampler is not None:
            block = resampler.process(block)
            if block.size == 0:
                return
        with self._sink_lock:
            sink = self._sink
            if sink is None:
                self._preroll.append(block)
                return
            try:
                sink(block)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка в обработчике аудио")
        try:
            self.on_level(level_from_block(block))
        except Exception:  # noqa: BLE001
            pass

    def _open(self, device) -> None:
        """Только в потоке управления."""
        sd = get_sd()
        if sd is None:
            raise RuntimeError(_backend_text())
        index, env = _resolve_device(device)
        with PA_LOCK, _TempEnv(env):
            try:
                stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK,
                                        device=index, callback=self._callback, latency="low")
                rate = SR
            except Exception:  # noqa: BLE001 — устройство не умеет 16 кГц напрямую
                info = sd.query_devices(index, "input")
                rate = int(info["default_samplerate"])
                stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                                        blocksize=int(BLOCK * rate / SR), device=index,
                                        callback=self._callback, latency="low")
                log.info("Микрофон открыт на %d Гц с пересчётом в 16 кГц", rate)
            self._native_rate = rate
            self._resampler = Resampler(rate) if rate != SR else None
            try:
                stream.start()
            except Exception:
                # Открылся, но не запустился (типично после сна) — обязательно закрыть,
                # иначе микрофон останется занятым и значок будет гореть.
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
                raise
        self._stream = stream
        self._device = device
        self.opened_at = time.monotonic()
        self.last_block_at = time.monotonic()
        log.info("Микрофон открыт (%s, %d Гц)", device or "системный", rate)

    def _close(self) -> None:
        """Только в потоке управления."""
        stream, self._stream = self._stream, None
        if stream is not None:
            with PA_LOCK:
                try:
                    stream.abort()          # не ждём буферы: закрываем сразу
                except Exception:  # noqa: BLE001
                    pass
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    log.debug("Ошибка закрытия микрофона", exc_info=True)
            log.info("Микрофон закрыт")
        self._preroll.clear()

    def _attach(self, sink, preroll_blocks: int) -> bool:
        """Подключить приёмник, если он всё ещё нужен (его могли остановить, пока микрофон открывался)."""
        with self._sink_lock:
            if self._wanted is not sink:
                return False
            preroll = list(self._preroll)[-preroll_blocks:] if preroll_blocks else []
            self._preroll.clear()
            for block in preroll:
                try:
                    sink(block)
                except Exception:  # noqa: BLE001
                    log.exception("Ошибка в обработчике аудио")
            self._sink = sink
            return True

    def _unused(self) -> bool:
        return self._sink is None and self._wanted is None and not self._warm

    def _close_if_unused_op(self) -> None:
        if self._stream is not None and self._unused():
            self._close()

    # ---------------------------------------------------------------- состояние
    @property
    def is_open(self) -> bool:
        return self._stream is not None

    @property
    def warm(self) -> bool:
        return self._warm

    @property
    def active(self) -> bool:
        return self._sink is not None

    # ---------------------------------------------------------------- API (главный поток)
    def start(self, device, sink: Callable[[np.ndarray], None], preroll_blocks: int = 8,
              on_result: Optional[Callable] = None, fallback: bool = False) -> None:
        """Подключить приёмник; микрофон открывается в фоне. on_result(ok, взят_системный, ошибка).
        fallback=True: выбранный микрофон не открылся — берём системный."""
        with self._sink_lock:
            self._wanted = sink
        if self.wedged:
            # Предыдущая операция висит — новые не копим: сразу сообщаем.
            if on_result:
                on_result(False, False, AudioHang("звуковая система не отвечает"))
            return

        def op():
            if self._wanted is not sink:
                if on_result:
                    on_result(True, False, None)      # уже остановили — открывать не нужно
                return
            if self._stream is not None and self._device != device:
                with self._sink_lock:
                    self._sink = None
                self._close()
            error, used_fallback = None, False
            if self._stream is None:
                try:
                    self._open(device)
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    if fallback and device is not None:
                        log.warning("Микрофон %r не открылся: %s — беру системный", device, exc)
                        try:
                            self._open(None)
                            error, used_fallback = None, True
                        except Exception:  # noqa: BLE001
                            pass
            if error is not None:
                with self._sink_lock:
                    if self._wanted is sink:
                        self._wanted = None
                if on_result:
                    on_result(False, False, error)
                return
            self._attach(sink, preroll_blocks)
            self._close_if_unused_op()
            if on_result:
                on_result(True, used_fallback, None)

        self._submit("open", op)

    def stop(self, sink=None, close: bool = True) -> bool:
        """Отключить приёмник (если передан — только его). Сразу: после возврата блоков в этот
        приёмник больше не будет. Поток закрывается в фоне, когда он больше никому не нужен
        (close=False — оставить открытым: сейчас подключится следующая запись).
        False — приёмник уже не наш."""
        with self._sink_lock:
            if sink is not None and self._wanted is not sink and self._sink is not sink:
                return False
            self._wanted = None
            self._sink = None
        if close and not self._warm:
            self._submit("close", self._close_if_unused_op)
        return True

    def reopen(self, device) -> None:
        """Переоткрыть поток с тем же приёмником (микрофон «замолчал»: USB, перезапуск PipeWire, сон).
        Не открылся выбранный — берём системный."""
        if self.wedged:
            return

        def op():
            with self._sink_lock:
                self._sink = None
            self._close()
            if self._wanted is None and not self._warm:
                return
            for candidate in ((device, None) if device is not None else (None,)):
                try:
                    self._open(candidate)
                    break
                except Exception as exc:  # noqa: BLE001
                    log.warning("Не удалось переоткрыть микрофон %r: %s", candidate, exc)
            else:
                return
            wanted = self._wanted
            if wanted is not None:
                self._attach(wanted, 0)

        self._submit("reopen", op)

    def set_warm(self, warm: bool, device=None) -> None:
        self._warm = warm
        if self.wedged:
            return

        def op():
            if self._warm and self._stream is None:
                try:
                    self._open(device)
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось держать микрофон открытым")
            elif not self._warm:
                self._close_if_unused_op()

        self._submit("warm" if warm else "cool", op)

    def close_if_unused(self) -> bool:
        """Страховка: поток открыт, а он никому не нужен — закрыть (значок микрофона гаснет)."""
        if self._stream is not None and self._unused() and not self.stuck_for():
            self._submit("close-unused", self._close_if_unused_op)
            return True
        return False


# ---------------------------------------------------------------- звуки
def _tone(freqs, duration, volume, sr=44100, attack=0.006, decay=None, glide=None):
    t = np.arange(int(sr * duration)) / sr
    if glide:
        f0, f1 = glide
        phase = 2 * np.pi * (f0 * t + (f1 - f0) * t ** 2 / (2 * duration))
        wave = np.sin(phase) + 0.25 * np.sin(2 * phase)
    else:
        wave = sum(np.sin(2 * np.pi * f * t) * (0.6 ** i) for i, f in enumerate(freqs))
    env = np.minimum(1.0, t / attack) * np.exp(-t / (decay or duration / 3.5))
    out = (wave * env * volume).astype(np.float32)
    fade = min(len(out), int(sr * 0.01))
    out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return out


def _seq(*parts, gap=0.0, sr=44100):
    pieces = []
    for p in parts:
        pieces.append(p)
        if gap:
            pieces.append(np.zeros(int(sr * gap), np.float32))
    return np.concatenate(pieces)


SOUNDS = {
    # Мягкий «капельный» сигнал: короткое скольжение вверх.
    "start": lambda v: _seq(_tone(None, 0.09, v, glide=(740, 1180)), _tone([1480], 0.12, v * 0.55)),
    "stop": lambda v: _seq(_tone(None, 0.08, v, glide=(1180, 760)), _tone([620], 0.11, v * 0.5)),
    "lock": lambda v: _seq(_tone([988], 0.06, v * 0.8), _tone([1318], 0.1, v * 0.8), gap=0.015),
    "cancel": lambda v: _tone(None, 0.14, v * 0.8, glide=(520, 300)),
    "error": lambda v: _seq(_tone([330], 0.09, v), _tone([262], 0.14, v), gap=0.03),
}


class SoundPlayer:
    """Звуковые сигналы и прослушивание записей — в своём потоке, по очереди. Если звуковая
    система не отвечает, сигнал просто пропускается: облачко и запись это не задерживает."""

    def __init__(self, volume: float = 0.35):
        self.volume = volume
        self._cache: dict = {}
        self._queue: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop_playback = threading.Event()

    def _submit(self, data: np.ndarray, rate: int) -> None:
        if self._queue.qsize() > 3:
            return          # поток воспроизведения занят (или звук завис) — не копим
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name="sounds", daemon=True)
            self._worker.start()
        self._queue.put((data, rate))

    def _run(self) -> None:
        while True:
            data, rate = self._queue.get()
            try:
                self._play_now(data, rate)
            except Exception:  # noqa: BLE001
                log.debug("Не удалось воспроизвести звук", exc_info=True)

    def _play_now(self, data: np.ndarray, rate: int) -> None:
        sd = get_sd()
        if sd is None or not PA_LOCK.acquire(timeout=1.0):
            return
        try:
            with _TempEnv({}):   # не открывать вывод, пока выставлен PIPEWIRE_NODE для микрофона
                stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32")
                stream.start()
        finally:
            PA_LOCK.release()
        try:
            self._stop_playback.clear()
            step = rate // 10
            for i in range(0, len(data), step):     # по 0,1 с — можно остановить
                if self._stop_playback.is_set():
                    break
                stream.write(data[i:i + step].reshape(-1, 1))
            time.sleep(0.05)
        finally:
            if PA_LOCK.acquire(timeout=1.0):
                try:
                    stream.abort()
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    PA_LOCK.release()

    def play(self, name: str) -> float:
        """Проиграть сигнал; возвращает длительность в секундах."""
        if name not in SOUNDS:
            return 0.0
        key = (name, round(self.volume, 2))
        data = self._cache.get(key)
        if data is None:
            data = self._cache[key] = SOUNDS[name](self.volume)
        self._submit(data, 44100)
        return data.size / 44100

    def play_array(self, audio: np.ndarray, rate: int = SR) -> None:
        """Прослушать запись из истории (предыдущая останавливается)."""
        self._stop_playback.set()
        self._submit(np.asarray(audio, np.float32), rate)


def wait_seconds(seconds: float) -> None:
    time.sleep(max(0.0, seconds))
