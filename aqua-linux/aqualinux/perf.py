"""Подстройка под мощность компьютера и режим питания.

Определяем железо (ядра, память, AVX2, видеокарта) и питание (батарея, профиль
«Энергосбережение» GNOME) и выбираем параметры:
  * частота анимации облачка: 144 Гц от сети → 60 Гц от батареи → 30 Гц на слабом ПК
    или в энергосбережении;
  * «живой» текст во время речи — реже на слабом процессоре и от батареи;
  * точность распознавания на процессоре: INT8 на слабых машинах (вдвое быстрее);
  * «тёплый» микрофон и локальная ИИ-модель не держатся в энергосбережении.

Всё вычисляется один раз и обновляется раз в 30 с (питание может смениться).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Profile:
    tier: str = "high"            # high | medium | low — мощность железа
    cores_physical: int = 4
    cores_logical: int = 8
    ram_gb: float = 8.0
    avx2: bool = True
    nvidia: bool = False
    on_battery: bool = False
    power_saver: bool = False
    battery_pct: int = 100

    @property
    def economy(self) -> bool:
        """Экономим: профиль «Энергосбережение», батарея почти разряжена или слабый ПК."""
        return self.power_saver or (self.on_battery and self.battery_pct <= 20) or self.tier == "low"

    def label(self) -> str:
        hw = {"high": "мощный", "medium": "средний", "low": "слабый"}[self.tier]
        power = "энергосбережение" if self.power_saver else ("от батареи" if self.on_battery else "от сети")
        n = self.cores_physical
        cores = "ядро" if n % 10 == 1 and n % 100 != 11 else ("ядра" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14
                                                              else "ядер")
        return f"Компьютер: {hw} ({n} {cores}, {self.ram_gb:.0f} ГБ" + \
            (", NVIDIA" if self.nvidia else "") + f"), питание: {power}"


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _physical_cores() -> int:
    cores = set()
    text = _read("/proc/cpuinfo")
    phys = core = None
    for line in text.splitlines():
        if line.startswith("physical id"):
            phys = line.split(":")[1].strip()
        elif line.startswith("core id"):
            core = line.split(":")[1].strip()
            cores.add((phys, core))
    return len(cores) or max(1, (os.cpu_count() or 2) // 2)


def _ram_gb() -> float:
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / 1024 / 1024
    return 8.0


def _power_state() -> tuple[bool, int, bool]:
    """(от батареи, заряд %, профиль «Энергосбережение»)."""
    on_ac, battery, pct = None, False, 100
    base = Path("/sys/class/power_supply")
    try:
        for dev in base.iterdir():
            kind = _read(str(dev / "type"))
            if kind == "Mains":
                on_ac = (on_ac or False) or _read(str(dev / "online")) == "1"
            elif kind == "Battery" and _read(str(dev / "scope")) != "Device":
                battery = True
                try:
                    pct = int(_read(str(dev / "capacity")) or 100)
                except ValueError:
                    pass
                if on_ac is None and _read(str(dev / "status")) == "Discharging":
                    on_ac = False
    except OSError:
        pass
    on_battery = battery and on_ac is False
    saver = False
    exe = shutil.which("powerprofilesctl")
    if exe:
        try:
            out = subprocess.run([exe, "get"], capture_output=True, text=True, timeout=2).stdout.strip()
            saver = out == "power-saver"
        except (OSError, subprocess.SubprocessError):
            pass
    return on_battery, pct, saver


_profile: Profile | None = None
_checked = 0.0
_lock = threading.Lock()


def detect(force: bool = False) -> Profile:
    global _profile, _checked
    with _lock:
        if _profile is None:
            cores = _physical_cores()
            ram = _ram_gb()
            avx2 = "avx2" in _read("/proc/cpuinfo")
            nvidia = shutil.which("nvidia-smi") is not None or Path("/proc/driver/nvidia/version").exists()
            if cores >= 6 and ram >= 12 and avx2:
                tier = "high"
            elif cores >= 4 and ram >= 6:
                tier = "medium"
            else:
                tier = "low"
            if nvidia and tier == "medium" and ram >= 8:
                tier = "high"
            _profile = Profile(tier, cores, os.cpu_count() or cores, ram, avx2, nvidia)
            log.info("%s", _profile.label())
        if force or not _checked:
            _checked = time.monotonic()
            _profile.on_battery, _profile.battery_pct, _profile.power_saver = _power_state()
        return _profile


def refresh_power() -> bool:
    """Перечитать питание (вызывается в фоне раз в ~30 с). True — что-то изменилось."""
    p = detect()
    state = _power_state()
    changed = state != (p.on_battery, p.battery_pct, p.power_saver)
    with _lock:
        p.on_battery, p.battery_pct, p.power_saver = state
    return changed


def override(settings) -> str:
    """Режим из настроек: auto | quality | economy."""
    return settings.get("general.performance", "auto") or "auto"


def economy(settings) -> bool:
    mode = override(settings)
    if mode == "economy":
        return True
    if mode == "quality":
        return False
    return detect().economy


def bubble_interval_ms(settings) -> int:
    """Период кадра анимации облачка."""
    if economy(settings):
        return 33                      # 30 Гц
    p = detect()
    if p.on_battery and override(settings) != "quality":
        return 16                      # 60 Гц
    return 7                           # ~144 Гц


def preview_interval_ms(settings) -> int:
    base = int(settings.get("asr.preview_interval_ms", 600) or 600)
    if economy(settings):
        return max(base, 1500)
    if detect().on_battery:
        return max(base, 1000)
    return base


def cpu_threads(settings) -> int:
    configured = int(settings.get("asr.cpu_threads", 0) or 0)
    p = detect()
    if configured > 0 and settings.get("general.performance", "auto") != "auto":
        return configured
    threads = p.cores_physical
    if economy(settings):
        threads = max(1, min(threads, 2))
    return max(1, min(threads, 8))


def cpu_precision() -> str:
    """Точность GigaAM на процессоре: INT8 вдвое быстрее, на мощном ПК — FP32 (чуть точнее)."""
    p = detect()
    return "fp32" if p.tier == "high" and p.avx2 else "int8"
