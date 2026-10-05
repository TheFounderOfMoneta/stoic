"""Микрофоны PipeWire: понятные названия, микрофон по умолчанию, усиление.

Источник данных — pactl (pipewire-pulse) в формате JSON, запасной — pw-dump.
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

TARGET_GAIN_DB = 20.0   # аппаратное усиление встроенного микрофона, при котором речь не перегружает АЦП


@dataclass
class Source:
    name: str               # alsa_input.pci-0000_05_00.6.analog-stereo
    description: str        # «Встроенное аудио Аналоговый стерео»
    default: bool = False
    volume_db: Optional[float] = None   # громкость в дБ относительно 100 %
    base_db: Optional[float] = None     # где у оборудования «0 дБ» (base volume)
    volume_pct: Optional[float] = None

    @property
    def gain_db(self) -> Optional[float]:
        """Сколько дБ усиления реально выставлено в микшере (Capture + Mic Boost)."""
        if self.volume_db is None or self.base_db is None:
            return None
        return self.volume_db - self.base_db


def _run(args: list[str], timeout: float = 2.0) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              env=_c_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _c_env() -> dict:
    import os
    env = dict(os.environ)
    env.pop("PIPEWIRE_NODE", None)
    env.pop("PULSE_SOURCE", None)
    return env


_DB = re.compile(r"(-?inf|-?\d+(?:[.,]\d+)?)\s*dB", re.IGNORECASE)
_PCT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def _parse_db(value) -> Optional[float]:
    if isinstance(value, dict):
        value = value.get("db")
    if value is None:
        return None
    m = _DB.search(str(value))
    if not m:
        return None
    text = m.group(1).replace(",", ".")
    if "inf" in text:
        return -math.inf
    return float(text)


def _parse_pct(value) -> Optional[float]:
    if isinstance(value, dict):
        value = value.get("value_percent")
    m = _PCT.search(str(value or ""))
    return float(m.group(1).replace(",", ".")) if m else None


def _channel_db(volume) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(volume, dict) or not volume:
        return None, None
    dbs = [_parse_db(ch) for ch in volume.values()]
    pcts = [_parse_pct(ch) for ch in volume.values()]
    dbs = [d for d in dbs if d is not None]
    pcts = [p for p in pcts if p is not None]
    return (max(dbs) if dbs else None), (max(pcts) if pcts else None)


def parse_pactl_sources(data: list, default_name: str = "") -> list[Source]:
    out = []
    for item in data or []:
        name = item.get("name") or ""
        if not name or name.endswith(".monitor"):
            continue
        monitor_of = item.get("monitor_of_sink")
        if monitor_of not in (None, "", "n/a"):
            continue
        props = item.get("properties") or {}
        if props.get("device.class") == "monitor":
            continue
        desc = item.get("description") or props.get("device.description") or name
        vol_db, vol_pct = _channel_db(item.get("volume"))
        out.append(Source(name, desc, name == default_name, vol_db, _parse_db(item.get("base_volume")), vol_pct))
    return out


def parse_pw_dump(data: list) -> list[Source]:
    default_name = ""
    for obj in data or []:
        if obj.get("type") == "PipeWire:Interface:Metadata" and \
                (obj.get("props") or {}).get("metadata.name") == "default":
            for entry in obj.get("metadata") or []:
                if entry.get("key") == "default.audio.source":
                    value = entry.get("value")
                    if isinstance(value, dict):
                        default_name = value.get("name") or ""
                    elif isinstance(value, str):
                        try:
                            default_name = json.loads(value).get("name", "")
                        except ValueError:
                            default_name = value
    out = []
    for obj in data or []:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        if not str(props.get("media.class", "")).startswith("Audio/Source"):
            continue
        name = props.get("node.name") or ""
        if not name:
            continue
        desc = props.get("node.description") or props.get("node.nick") or name
        out.append(Source(name, desc, name == default_name))
    return out


_cache: tuple[float, list[Source]] = (0.0, [])
_cache_lock = threading.Lock()


def list_sources(max_age: float = 3.0) -> list[Source]:
    """Микрофоны (без «мониторов» выходов). Кэш на несколько секунд — вызывается часто."""
    global _cache
    with _cache_lock:
        if time.monotonic() - _cache[0] < max_age:
            return list(_cache[1])
        sources: list[Source] = []
        pactl = shutil.which("pactl")
        if pactl:
            raw = _run([pactl, "--format=json", "list", "sources"])
            default = _run([pactl, "get-default-source"]).strip()
            try:
                sources = parse_pactl_sources(json.loads(raw), default) if raw.strip() else []
            except ValueError:
                sources = []
        if not sources and shutil.which("pw-dump"):
            raw = _run(["pw-dump"], timeout=3.0)
            try:
                sources = parse_pw_dump(json.loads(raw)) if raw.strip() else []
            except ValueError:
                sources = []
        _cache = (time.monotonic(), sources)
        return list(sources)


def invalidate() -> None:
    global _cache
    with _cache_lock:
        _cache = (0.0, [])


def current_source(setting_value) -> Optional[Source]:
    """Источник, который сейчас пишет приложение (выбранный или системный)."""
    sources = list_sources()
    if isinstance(setting_value, str) and setting_value.startswith("pw:"):
        for src in sources:
            if src.name == setting_value[3:]:
                return src
    return next((s for s in sources if s.default), None)


def fix_gain(setting_value=None, clipped_ratio: float = 0.0) -> tuple[bool, str]:
    """Снизить усиление микрофона до разумного (≈ +20 дБ аппаратного усиления).

    Громкость источника PipeWire — это сумма Capture и Mic Boost в микшере ALSA:
    при «100 %» у встроенных микрофонов бывает +60 дБ, и речь упирается в потолок.
    Возвращает (успех, понятное сообщение).
    """
    invalidate()
    src = current_source(setting_value)
    pactl = shutil.which("pactl")
    wpctl = shutil.which("wpctl")
    if src is not None and pactl:
        gain = src.gain_db
        if gain is not None and src.volume_db is not None and math.isfinite(gain):
            target = min(TARGET_GAIN_DB, gain - 6.0)
            new_db = src.base_db + target
            new_db = min(new_db, 0.0)   # выше 100 % не поднимаем
            linear = 10 ** (new_db / 20.0)
            _run([pactl, "set-source-volume", src.name, f"{linear:.6f}"])
            invalidate()
            return True, (f"Усиление микрофона снижено с +{gain:.0f} до +{max(0.0, target):.0f} дБ. "
                          f"Проверьте уровень на главной странице.")
        # Нет сведений о «0 дБ» оборудования — уменьшаем на 15 дБ от текущего.
        if src.volume_db is not None and math.isfinite(src.volume_db):
            new_db = src.volume_db - 15.0
            _run([pactl, "set-source-volume", src.name, f"{10 ** (new_db / 20.0):.6f}"])
            invalidate()
            return True, "Громкость микрофона уменьшена на 15 дБ."
    if wpctl:
        target = "@DEFAULT_AUDIO_SOURCE@"
        if src is not None and isinstance(setting_value, str) and setting_value.startswith("pw:"):
            node_id = _wpctl_id(src.name)
            target = node_id or target
        out = _run([wpctl, "get-volume", target])
        m = re.search(r"Volume:\s*([\d.]+)", out)
        if m:
            current = float(m.group(1))
            new = max(0.05, current * 10 ** (-15.0 / 60.0))   # кубическая шкала wpctl: −15 дБ
            _run([wpctl, "set-volume", target, f"{new:.3f}"])
            invalidate()
            return True, f"Громкость микрофона уменьшена: {current * 100:.0f} % → {new * 100:.0f} %."
    return False, ("Не удалось изменить усиление автоматически. Откройте Настройки Ubuntu → Звук → "
                   "Вход и уменьшите громкость микрофона примерно до трети.")


def _wpctl_id(node_name: str) -> Optional[str]:
    raw = _run(["pw-dump"], timeout=3.0) if shutil.which("pw-dump") else ""
    try:
        for obj in json.loads(raw) if raw.strip() else []:
            props = (obj.get("info") or {}).get("props") or {}
            if props.get("node.name") == node_name:
                return str(obj.get("id"))
    except ValueError:
        pass
    return None


def gain_report(setting_value=None) -> str:
    src = current_source(setting_value)
    if src is None:
        return ""
    gain = src.gain_db
    if gain is None or not math.isfinite(gain):
        return src.description
    return f"{src.description}: усиление +{gain:.0f} дБ"
