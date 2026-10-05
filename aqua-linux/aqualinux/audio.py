"""Захват микрофона (PipeWire через PortAudio) и звуковые сигналы."""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

SR = 16000
BLOCK = 512  # 32 мс — совпадает с кадром Silero VAD

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


def list_input_devices() -> list[tuple[Optional[int], str]]:
    """[(index|None, name)] — None означает системный микрофон по умолчанию."""
    out: list[tuple[Optional[int], str]] = [(None, "Системный по умолчанию (PipeWire)")]
    if sd is None:
        return out
    try:
        for index, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                out.append((index, dev["name"]))
    except Exception:  # noqa: BLE001
        log.exception("Не удалось получить список устройств")
    return out


def _resolve_device(name_or_index):
    """В настройках храним имя устройства — индекс меняется при переподключении."""
    if name_or_index is None or sd is None:
        return None
    if isinstance(name_or_index, int):
        return name_or_index
    for index, dev in enumerate(sd.query_devices()):
        if dev["name"] == name_or_index and dev.get("max_input_channels", 0) > 0:
            return index
    return None


def level_from_block(block: np.ndarray) -> float:
    """RMS → 0..1 по шкале -58…-8 дБФС."""
    rms = float(np.sqrt(np.mean(np.square(block), dtype=np.float64))) if block.size else 0.0
    db = 20.0 * math.log10(rms + 1e-9)
    return float(min(1.0, max(0.0, (db + 58.0) / 50.0)))


class Recorder:
    """Микрофон → колбэк блоков 16 кГц mono float32 + уровень громкости.

    on_audio(block) вызывается из потока PortAudio; on_level(level) — тоже.
    """

    def __init__(self, on_audio: Callable[[np.ndarray], None], on_level: Callable[[float], None]):
        self.on_audio = on_audio
        self.on_level = on_level
        self._stream = None
        self._lock = threading.Lock()
        self._active = False
        self._device = None
        self._native_rate = SR
        self._resample_buf = np.zeros(0, np.float32)
        self._preroll: deque = deque(maxlen=12)   # ~0,4 с перед нажатием при «тёплом» микрофоне
        self._warm = False

    # --------------------------------------------------------------- поток
    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        block = indata[:, 0].astype(np.float32, copy=True)
        if self._native_rate != SR:
            block = self._resample(block)
            if block.size == 0:
                return
        if not self._active:
            self._preroll.append(block)
            return
        try:
            self.on_level(level_from_block(block))
            self.on_audio(block)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в обработчике аудио")

    def _resample(self, block: np.ndarray) -> np.ndarray:
        data = np.concatenate([self._resample_buf, block])
        ratio = self._native_rate / SR
        n_out = int(data.size / ratio)
        if n_out <= 1:
            self._resample_buf = data
            return np.zeros(0, np.float32)
        if ratio > 1.0:
            # Простейший ФНЧ (скользящее среднее) против наложения спектра.
            k = max(1, int(round(ratio)))
            if k > 1:
                data_f = np.convolve(data, np.ones(k, np.float32) / k, mode="same")
            else:
                data_f = data
        else:
            data_f = data
        x_old = np.arange(data.size, dtype=np.float64)
        x_new = np.arange(n_out, dtype=np.float64) * ratio
        out = np.interp(x_new, x_old, data_f).astype(np.float32)
        consumed = int(n_out * ratio)
        self._resample_buf = data[consumed:]
        return out

    def _open(self, device) -> None:
        if sd is None:
            raise RuntimeError(audio_backend_error())
        index = _resolve_device(device)
        try:
            stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK,
                                    device=index, callback=self._callback, latency="low")
            self._native_rate = SR
        except Exception:  # noqa: BLE001 — устройство не умеет 16 кГц напрямую
            info = sd.query_devices(index, "input")
            rate = int(info["default_samplerate"])
            stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                                    blocksize=int(BLOCK * rate / SR), device=index,
                                    callback=self._callback, latency="low")
            self._native_rate = rate
            log.info("Микрофон открыт на %d Гц с ресемплингом", rate)
        stream.start()
        self._stream = stream
        self._device = device

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                log.exception("Ошибка закрытия микрофона")

    # ---------------------------------------------------------------- API
    def set_warm(self, warm: bool, device=None) -> None:
        with self._lock:
            self._warm = warm
            if warm and self._stream is None:
                try:
                    self._open(device)
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось держать микрофон открытым")
            elif not warm and not self._active:
                self._close()

    def start(self, device=None) -> None:
        with self._lock:
            if self._stream is not None and self._device != device:
                self._close()
            self._resample_buf = np.zeros(0, np.float32)
            if self._stream is None:
                self._preroll.clear()
                self._open(device)
            preroll = list(self._preroll)
            self._preroll.clear()
            self._active = True
        for block in preroll[-8:]:
            self.on_audio(block)

    def stop(self) -> None:
        with self._lock:
            self._active = False
            if not self._warm:
                self._close()

    @property
    def active(self) -> bool:
        return self._active


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
                    sd.play(data, 44100, blocking=True)
                except Exception:  # noqa: BLE001
                    log.debug("Не удалось воспроизвести звук", exc_info=True)

        threading.Thread(target=worker, daemon=True).start()
        return data.size / 44100


def wait_seconds(seconds: float) -> None:
    time.sleep(max(0.0, seconds))
