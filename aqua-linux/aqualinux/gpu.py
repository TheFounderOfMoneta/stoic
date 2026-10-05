"""Видеопамять: сколько свободно и кто её занял (Ollama, llama-server, игры, браузер…)."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

ASR_NEED_MB = 900   # GigaAM INT8 + CUDA Graphs + рабочая память на фрагмент до 18 с


@dataclass
class GpuUser:
    pid: int
    name: str
    used_mb: int


def _smi(args: list[str]) -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=4).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def memory() -> Optional[tuple[int, int, int]]:
    """(всего, занято, свободно) МБ по nvidia-smi; None — нет NVIDIA."""
    out = _smi(["--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"])
    try:
        total, used, free = (int(float(x)) for x in out.splitlines()[0].split(","))
        return total, used, free
    except (IndexError, ValueError):
        return None


def _pretty(pid: int, raw: str) -> str:
    name = os.path.basename(raw.strip()) or raw.strip()
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        cmd = raw
    low = (name + " " + cmd).lower()
    if "ollama" in low:
        return "Ollama"
    if "llama-server" in low and "aqua-linux" in low:
        return "ИИ Aqua Linux (Qwen)"
    if "llama-server" in low or "llama-cli" in low:
        return "llama.cpp"
    if "aqualinux" in low:
        return "Aqua Linux (распознавание)"
    if "python" in low:
        return f"Python ({cmd.split()[1] if len(cmd.split()) > 1 else name})"[:60]
    return name


def users() -> list[GpuUser]:
    """Процессы, которые держат видеопамять (по убыванию)."""
    out = _smi(["--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"])
    result = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            pid, used = int(parts[0]), int(float(parts[2]))
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        result.append(GpuUser(pid, _pretty(pid, parts[1]), used))
    result.sort(key=lambda u: -u.used_mb)
    return result


def gb(mb: float) -> str:
    return f"{mb / 1024:.1f}".replace(".", ",") + " ГБ"


def describe(free_mb: Optional[int] = None, need_mb: int = ASR_NEED_MB) -> str:
    """Понятное объяснение: «Видеопамять занята: Ollama — qwen3:4b (3,1 ГБ)…»."""
    parts = []
    ollama_models = []
    try:
        from .corrector import ollama_loaded
        ollama_models = ollama_loaded()
    except Exception:  # noqa: BLE001
        pass
    for u in users():
        if u.used_mb < 64:
            continue
        label = u.name
        if u.name == "Ollama" and ollama_models:
            label += " — " + ", ".join(m["name"] for m in ollama_models)
        parts.append(f"{label} ({gb(u.used_mb)})")
    if not parts and ollama_models:
        parts = [f"Ollama — {m['name']} ({gb(m['vram_mb'])})" for m in ollama_models if m["vram_mb"]]
    head = "Видеопамять занята другими программами"
    if free_mb is not None:
        head += f": свободно {gb(free_mb)}, нужно ~{gb(need_mb)}"
    if parts:
        head += ". Заняли: " + "; ".join(parts[:4])
    return head + "."
