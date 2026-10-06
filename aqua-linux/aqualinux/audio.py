"""Захват микрофона (PipeWire через PortAudio) и звуковые сигналы."""
from __future__ import annotations

import logging
import math
import os
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

try:
    import sounddevice as sd
except (ImportError, OSError) as exc:  # нет libportaudio2
    sd = None
    _SD_ERROR = exc
else:
    _SD_ERROR = None


def audio_backend_error() -> Optional[str]:
    if sd is None:
        return (f"Не найден PortAudio ({_SD_ERROR}). Установите пакет: sudo apt install libportaudio2")
    return None


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
    on_level(level) вызывается из потока PortAudio.
    """

    def __init__(self, on_level: Callable[[float], None]):
        self.on_level = on_level
        self._stream = None
        self._lock = threading.Lock()        # открытие/закрытие потока
        self._sink_lock = threading.Lock()   # смена приёмника (короткая, без закрытия потока)
        self._sink: Optional[Callable[[np.ndarray], None]] = None
        self._device = None
        self._native_rate = SR
        self._resampler: Optional[Resampler] = None
        self._preroll: deque = deque(maxlen=12)   # ~0,4 с перед нажатием при «тёплом» микрофоне
        self._warm = False
        self.opened_at = 0.0
        self.last_block_at = 0.0

    # --------------------------------------------------------------- поток
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
        if sd is None:
            raise RuntimeError(audio_backend_error())
        index, env = _resolve_device(device)
        with _TempEnv(env):
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
        stream, self._stream = self._stream, None
        if stream is not None:
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

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    @property
    def warm(self) -> bool:
        return self._warm

    def close_if_unused(self) -> bool:
        """Страховка: поток открыт, а он никому не нужен — закрыть (значок микрофона гаснет)."""
        with self._lock:
            if self._stream is not None and self._sink is None and not self._warm:
                self._close()
                return True
        return False

    # ---------------------------------------------------------------- API
    def set_warm(self, warm: bool, device=None) -> None:
        with self._lock:
            self._warm = warm
            if warm and self._stream is None:
                try:
                    self._open(device)
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось держать микрофон открытым")
            elif not warm and self._sink is None:
                self._close()

    def start(self, device, sink: Callable[[np.ndarray], None], preroll_blocks: int = 8) -> None:
        """Подключить приёмник. Если поток уже открыт на этом устройстве — без переоткрытия."""
        with self._lock:
            if self._stream is not None and self._device != device:
                with self._sink_lock:
                    self._sink = None
                self._close()
            if self._stream is None:
                self._open(device)
            with self._sink_lock:
                preroll = list(self._preroll)[-preroll_blocks:] if preroll_blocks else []
                self._preroll.clear()
                for block in preroll:
                    try:
                        sink(block)
                    except Exception:  # noqa: BLE001
                        log.exception("Ошибка в обработчике аудио")
                self._sink = sink

    def reopen(self, device) -> bool:
        """Переоткрыть поток с тем же приёмником (микрофон «замолчал»: USB, перезапуск PipeWire)."""
        with self._lock:
            with self._sink_lock:
                sink = self._sink
                self._sink = None
            self._close()
            try:
                self._open(device)
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось переоткрыть микрофон %r: %s", device, exc)
                with self._sink_lock:
                    self._sink = sink
                return False
            with self._sink_lock:
                self._sink = sink
            return True

    def stop(self, sink=None, close: bool = True) -> bool:
        """Отключить приёмник (если передан — только его). Поток закрывается, когда
        он больше никому не нужен и микрофон не держится «тёплым» (close=False — оставить
        открытым: сейчас подключится следующая запись). False — приёмник уже не наш."""
        with self._lock:
            with self._sink_lock:
                if sink is not None and self._sink is not sink:
                    return False
                self._sink = None
            if close and not self._warm:
                self._close()
            return True

    @property
    def active(self) -> bool:
        return self._sink is not None


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
    def __init__(self, volume: float = 0.35):
        self.volume = volume
        self._cache: dict = {}
        self._lock = threading.Lock()

    def play(self, name: str) -> float:
        """Проиграть сигнал; возвращает длительность в секундах."""
        if sd is None or name not in SOUNDS:
            return 0.0
        key = (name, round(self.volume, 2))
        data = self._cache.get(key)
        if data is None:
            data = self._cache[key] = SOUNDS[name](self.volume)

        def worker():
            with self._lock:
                try:
                    with _TempEnv({}):   # не открывать вывод, пока выставлен PIPEWIRE_NODE для микрофона
                        stream = sd.OutputStream(samplerate=44100, channels=1, dtype="float32")
                        stream.start()
                    stream.write(data.reshape(-1, 1))
                    time.sleep(0.05)
                    stream.stop()
                    stream.close()
                except Exception:  # noqa: BLE001
                    log.debug("Не удалось воспроизвести звук", exc_info=True)

        threading.Thread(target=worker, daemon=True).start()
        return data.size / 44100


def wait_seconds(seconds: float) -> None:
    time.sleep(max(0.0, seconds))
