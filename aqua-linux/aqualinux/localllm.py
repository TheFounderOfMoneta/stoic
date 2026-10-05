"""Встроенная языковая модель: Qwen3.5-0.8B через llama.cpp (llama-server).

Aqua сама скачивает готовую сборку llama.cpp (CUDA 12.8 с рантаймом CUDA рядом —
CUDA Toolkit не нужен; запасной вариант — Vulkan или процессор) и GGUF-веса
модели, запускает сервер на 127.0.0.1 и держит модель в видеопамяти, чтобы
исправление текста занимало доли секунды.

CLI:  python -m aqualinux.localllm install [--enable] | status | test "текст"
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .config import DATA_DIR, STATE_DIR, Settings

log = logging.getLogger(__name__)

LLM_DIR = DATA_DIR / "llm"
MODEL_DIR = LLM_DIR / "models"
SERVER_LOG = STATE_DIR / "llama-server.log"
GITHUB_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
RELEASE_DL = "https://github.com/ggml-org/llama.cpp/releases/download"
FALLBACK_TAG = "b11425"          # проверенная сборка, если API GitHub недоступен
HF = "https://huggingface.co"
MODEL_REPOS = ("ggml-org/Qwen3.5-0.8B-GGUF", "unsloth/Qwen3.5-0.8B-GGUF", "bartowski/Qwen_Qwen3.5-0.8B-GGUF")
QUANTS = ("Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0")
USER_AGENT = "aqua-linux/1.0"

# Шаблоны имён файлов в релизах llama.cpp (см. .github/workflows/release.yml).
ASSETS = {
    "cuda": [r"^llama-(?P<tag>b\d+)-bin-ubuntu-cuda-12\.\d+-x64\.(tar\.gz|zip)$",
             r"^cudart-llama-(?P<tag>b\d+)-bin-ubuntu-cuda-12\.\d+-x64\.(tar\.gz|zip)$"],
    "vulkan": [r"^llama-(?P<tag>b\d+)-bin-ubuntu-vulkan-x64\.(tar\.gz|zip)$"],
    "cpu": [r"^llama-(?P<tag>b\d+)-bin-ubuntu-x64\.(tar\.gz|zip)$"],
}
FALLBACK_FILES = {
    "cuda": [f"llama-{FALLBACK_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz",
             f"cudart-llama-{FALLBACK_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz"],
    "vulkan": [f"llama-{FALLBACK_TAG}-bin-ubuntu-vulkan-x64.tar.gz"],
    "cpu": [f"llama-{FALLBACK_TAG}-bin-ubuntu-x64.tar.gz"],
}


class InstallError(RuntimeError):
    pass


# ---------------------------------------------------------------- загрузки
def _open(url: str, headers: Optional[dict] = None, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def _download_once(url: str, dest: Path, progress: Optional[Callable[[int, int], None]]) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    done = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={done}-"} if done else {}
    try:
        resp = _open(url, headers, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and done:   # уже всё скачано
            part.rename(dest)
            return dest
        raise
    with resp:
        if done and resp.status != 206:
            done = 0   # сервер не умеет Range — начинаем заново
        total = int(resp.headers.get("Content-Length") or 0) + done
        with open(part, "ab" if done else "wb") as fh:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                fh.write(block)
                done += len(block)
                if progress:
                    progress(done, total)
    if total and done < total:
        raise InstallError(f"Загрузка оборвалась: {dest.name} ({done} из {total} байт)")
    os.replace(part, dest)
    return dest


def download(url: str, dest: Path, progress: Optional[Callable[[int, int], None]] = None,
             attempts: int = 6, on_retry: Optional[Callable[[int, str], None]] = None) -> Path:
    """Скачивание с докачкой (.part + Range) и автоматическими повторами при обрыве.

    После обрыва продолжаем с того же места: 1, 2, 4, 8, 16 с паузы между попытками.
    Ошибки 404/403 не повторяем — файла просто нет.
    """
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return _download_once(url, dest, progress)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404, 410) or attempt == attempts:
                raise
            reason = f"HTTP {exc.code}"
        except (InstallError, OSError, TimeoutError) as exc:
            if attempt == attempts:
                raise InstallError(f"Не удалось скачать {dest.name} за {attempts} попыток: {exc}") from exc
            reason = str(exc) or exc.__class__.__name__
        log.warning("Загрузка %s прервалась (%s) — повтор %d/%d через %.0f с",
                    dest.name, reason, attempt + 1, attempts, delay)
        if on_retry:
            on_retry(attempt + 1, reason)
        time.sleep(delay)
        delay = min(delay * 2, 16.0)
    raise InstallError(f"Не удалось скачать {dest.name}")


def extract(archive: Path, target: Path) -> None:
    """Распаковать, убрав верхний каталог архива (llama-bNNNN/)."""
    target.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(tmp, filter="tar") if sys.version_info >= (3, 12) else tf.extractall(tmp)
    entries = list(tmp.iterdir())
    root = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp
    for item in root.iterdir():
        dest = target / item.name
        if dest.exists() or dest.is_symlink():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        shutil.move(str(item), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    for exe in target.iterdir():
        if exe.is_file() and not exe.suffix and not exe.name.startswith("lib"):
            exe.chmod(0o755)


def release_assets(backend: str) -> list[tuple[str, str]]:
    """[(имя файла, url)] для сборки llama.cpp. Берём свежий релиз, где есть все нужные файлы."""
    patterns = [re.compile(p) for p in ASSETS[backend]]
    try:
        with _open(GITHUB_RELEASES, {"Accept": "application/vnd.github+json"}, timeout=20) as resp:
            releases = json.loads(resp.read().decode())
        for rel in releases:
            if rel.get("draft") or rel.get("prerelease"):
                continue
            names = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
            found = []
            for pattern in patterns:
                match = next((n for n in sorted(names) if pattern.match(n)), None)
                if match:
                    found.append((match, names[match]))
            if len(found) == len(patterns):
                return found
    except Exception as exc:  # noqa: BLE001 — нет сети к API или лимит запросов
        log.warning("GitHub API недоступен (%s), беру сборку %s", exc, FALLBACK_TAG)
    return [(name, f"{RELEASE_DL}/{FALLBACK_TAG}/{name}") for name in FALLBACK_FILES[backend]]


def find_local_gguf(quant: str = "Q4_K_M") -> Optional[Path]:
    """Уже скачанный Qwen3.5-0.8B*.gguf в домашнем каталоге — чтобы ничего не качать."""
    home = Path.home()
    try:
        out = subprocess.run(["find", str(home), "-xdev", "-maxdepth", "7", "-type", "f", "-iname",
                              "*qwen3.5-0.8b*.gguf", "-not", "-iname", "*mmproj*", "-size", "+100M"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:  # noqa: BLE001
        return None
    found = [Path(line) for line in out.splitlines() if line.strip() and "/.local/share/aqua-linux/" not in line]
    if not found:
        return None
    found.sort(key=lambda p: (quant.lower() not in p.name.lower(), len(str(p))))
    return found[0]


def model_file(repo_override: str = "", quant: str = "Q4_K_M") -> tuple[str, str]:
    """(имя файла, url) GGUF-весов Qwen3.5-0.8B.

    Сначала ищем нужный квант (Q4_K_M, ~0,5 ГБ) во ВСЕХ репозиториях и только потом
    переходим к более тяжёлым — иначе первый репозиторий без Q4 отдал бы Q8 (~0,8 ГБ).
    """
    repos = [repo_override] if repo_override else list(MODEL_REPOS)
    quants = [quant] + [q for q in QUANTS if q != quant]
    errors = []
    listing: dict[str, list[str]] = {}
    for repo in repos:
        for attempt in range(3):
            try:
                with _open(f"{HF}/api/models/{repo}", timeout=20) as resp:
                    files = [s["rfilename"] for s in json.loads(resp.read().decode()).get("siblings", [])]
                listing[repo] = [f for f in files if f.lower().endswith(".gguf") and "mmproj" not in f.lower()]
                break
            except urllib.error.HTTPError as exc:
                errors.append(f"{repo}: HTTP {exc.code}")
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    errors.append(f"{repo}: {exc}")
                time.sleep(1 + attempt)
    for q in quants:
        for repo in repos:
            match = next((f for f in listing.get(repo, [])
                          if re.search(rf"[-_.]{re.escape(q)}\.gguf$", f, re.IGNORECASE)), None)
            if match:
                return Path(match).name, f"{HF}/{repo}/resolve/main/{match}"
    raise InstallError("Не нашёл GGUF-файл Qwen3.5-0.8B на Hugging Face. " + "; ".join(errors))


# ---------------------------------------------------------------- уже установленное
CUDA_LIBS = ("libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12")
KNOWN_VENVS = (Path("/home/vlad/Documents/Codex/2026-10-05/new-chat-3/outputs/gigaam/.venv"),)


def find_cuda_libs() -> dict[str, Path]:
    """CUDA 12 runtime, который уже есть на компьютере: Ollama, PyTorch (pip nvidia-*), системный."""
    dirs: list[Path] = []
    for root in (Path("/usr/local/lib/ollama"), Path("/usr/lib/ollama"), Path.home() / ".ollama/lib"):
        if root.is_dir():
            dirs += sorted(p.parent for p in root.rglob("libcudart.so.12*"))
            dirs += sorted(p.parent for p in root.rglob("libcublas.so.12*"))
    site_dirs = [Path(p) for p in sys.path if p.endswith("site-packages")]
    for venv in KNOWN_VENVS:
        site_dirs += list(venv.glob("lib/python3*/site-packages"))
    for site in site_dirs:
        dirs += [site / "nvidia/cuda_runtime/lib", site / "nvidia/cublas/lib"]
    dirs += [Path(p) for p in ("/usr/local/cuda/lib64", "/usr/lib/x86_64-linux-gnu")]
    found: dict[str, Path] = {}
    for name in CUDA_LIBS:
        for d in dirs:
            hits = sorted(d.glob(name + "*")) if d.is_dir() else []
            if hits:
                found[name] = hits[0].resolve()
                break
    return found if len(found) == len(CUDA_LIBS) else {}


def find_llama_server() -> Optional[Path]:
    """Уже собранный/установленный llama-server с поддержкой видеокарты."""
    candidates = [shutil.which("llama-server")]
    home = Path.home()
    for rel in ("llama.cpp/build/bin/llama-server", "llama.cpp/llama-server", ".local/bin/llama-server",
                "src/llama.cpp/build/bin/llama-server"):
        candidates.append(str(home / rel))
    for c in candidates:
        if not c or not Path(c).is_file() or "aqua-linux" in c:
            continue
        try:
            out = subprocess.run([c, "--list-devices"], capture_output=True, text=True, timeout=20)
            if re.search(r"(CUDA|Vulkan)\d+:.*(nvidia|geforce|gtx|rtx)", out.stdout + out.stderr, re.IGNORECASE):
                return Path(c).resolve()
        except Exception:  # noqa: BLE001
            continue
    return None


# ---------------------------------------------------------------- железо
def detect_backend() -> str:
    if Path("/proc/driver/nvidia/version").exists() or shutil.which("nvidia-smi"):
        return "cuda"
    try:
        out = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=3).stdout
        if "libvulkan.so.1" in out:
            return "vulkan"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _set_pdeathsig() -> None:
    """Сервер завершится вместе с Aqua, даже если Aqua упадёт."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- менеджер
class LocalLLM:
    """Состояния: absent | installing | starting | ready | error | stopped.

    on_state(state, message, progress 0..1 или -1) вызывается из рабочих потоков.
    """

    def __init__(self, settings: Settings, on_state: Callable[[str, str, float], None] = lambda *a: None):
        self.settings = settings
        self.on_state = on_state
        self.state = "absent" if not self.installed() else "stopped"
        self.message = ""
        self.port = 0
        self.proc: Optional[subprocess.Popen] = None
        self.backend = ""
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stopping = False

    # ------------------------------------------------------------ пути
    def wanted_backend(self) -> str:
        choice = self.settings.get("llm.builtin_backend", "auto") or "auto"
        return detect_backend() if choice == "auto" else choice

    def engine_dir(self, backend: str) -> Path:
        return LLM_DIR / f"engine-{backend}"

    def server_path(self, backend: str) -> Path:
        return self.engine_dir(backend) / "llama-server"

    def model_path(self) -> Optional[Path]:
        if not MODEL_DIR.exists():
            return None
        files = sorted(p for p in MODEL_DIR.glob("*.gguf") if "mmproj" not in p.name.lower())
        return files[0] if files else None

    def installed(self, backend: Optional[str] = None) -> bool:
        backend = backend or self.wanted_backend()
        if backend == "cuda" and not self.server_path(backend).is_symlink() \
                and not (self.engine_dir(backend) / "libcudart.so.12").exists():
            return False   # движок есть, а рантайма CUDA рядом нет (например, загрузку прервали)
        return self.server_path(backend).exists() and self.model_path() is not None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def ready(self) -> bool:
        return self.state == "ready" and self.proc is not None and self.proc.poll() is None

    def _set(self, state: str, message: str = "", progress: float = -1) -> None:
        self.state, self.message = state, message
        log.info("Локальный ИИ: %s %s", state, message)
        try:
            self.on_state(state, message, progress)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка обработчика состояния ИИ")

    # ------------------------------------------------------------ установка
    def install(self, backend: Optional[str] = None) -> None:
        backend = backend or self.wanted_backend()
        LLM_DIR.mkdir(parents=True, exist_ok=True)
        engine = self.engine_dir(backend)
        if not self.server_path(backend).exists() and backend != "cpu":
            existing = find_llama_server()
            if existing is not None:
                engine.mkdir(parents=True, exist_ok=True)
                self.server_path(backend).symlink_to(existing)
                log.info("Использую уже установленный llama-server: %s", existing)
        if not self.server_path(backend).exists():
            assets = release_assets(backend)
            for name, url in assets:
                is_cudart = name.startswith("cudart")
                if is_cudart and self._link_cuda_libs(engine):
                    continue   # CUDA уже есть на компьютере — 600 МБ не качаем
                label = "рантайм CUDA" if is_cudart else "движок llama.cpp"
                archive = LLM_DIR / "downloads" / name
                if not archive.exists():
                    download(url, archive, lambda d, t, label=label: self._set(
                        "installing", f"Скачиваю {label}… {d / 1e6:.0f} из {t / 1e6:.0f} МБ", d / t if t else -1))
                self._set("installing", f"Распаковываю {label}…")
                extract(archive, engine)
                archive.unlink(missing_ok=True)
            if not self.server_path(backend).exists():
                raise InstallError("В архиве llama.cpp нет llama-server")
        if backend == "cuda" and not self.server_path(backend).is_symlink() \
                and not (engine / "libcudart.so.12").exists() and not self._link_cuda_libs(engine):
            for name, url in release_assets(backend):
                if name.startswith("cudart"):
                    archive = LLM_DIR / "downloads" / name
                    download(url, archive, lambda d, t: self._set(
                        "installing", f"Скачиваю рантайм CUDA… {d / 1e6:.0f} из {t / 1e6:.0f} МБ",
                        d / t if t else -1))
                    extract(archive, engine)
                    archive.unlink(missing_ok=True)
        if self.model_path() is None:
            self._set("installing", "Ищу модель Qwen3.5-0.8B…")
            quant = self.settings.get("llm.builtin_quant") or "Q4_K_M"
            local = find_local_gguf(quant)
            if local is not None:
                MODEL_DIR.mkdir(parents=True, exist_ok=True)
                (MODEL_DIR / local.name).symlink_to(local)
                log.info("Использую уже скачанную модель: %s", local)
                return
            name, url = model_file(self.settings.get("llm.builtin_repo") or "", quant)
            download(url, MODEL_DIR / name, lambda d, t: self._set(
                "installing", f"Скачиваю модель Qwen3.5-0.8B… {d / 1e6:.0f} из {t / 1e6:.0f} МБ", d / t if t else -1))

    def _link_cuda_libs(self, engine: Path) -> bool:
        libs = find_cuda_libs()
        if not libs:
            return False
        engine.mkdir(parents=True, exist_ok=True)
        for name, path in libs.items():
            target = engine / name
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(path)
        (engine / ".cuda-libs-reused").write_text("\n".join(str(p) for p in libs.values()))
        log.info("CUDA уже установлена — использую: %s", ", ".join(str(p) for p in libs.values()))
        return True

    def _download_cudart(self, backend: str) -> bool:
        """Если с найденной CUDA сервер не стартовал — докачиваем родной рантайм llama.cpp."""
        engine = self.engine_dir(backend)
        marker = engine / ".cuda-libs-reused"
        if not marker.exists():
            return False
        marker.unlink()
        for name, url in release_assets(backend):
            if not name.startswith("cudart"):
                continue
            for lib in CUDA_LIBS:
                if (engine / lib).is_symlink():
                    (engine / lib).unlink()
            archive = LLM_DIR / "downloads" / name
            download(url, archive, lambda d, t: self._set(
                "installing", f"Скачиваю рантайм CUDA… {d / 1e6:.0f} из {t / 1e6:.0f} МБ", d / t if t else -1))
            extract(archive, engine)
            archive.unlink(missing_ok=True)
            return True
        return False

    def remove(self) -> None:
        self.stop()
        shutil.rmtree(LLM_DIR, ignore_errors=True)
        self._set("absent", "")

    # ------------------------------------------------------------ сервер
    def _command(self, backend: str, port: int) -> list[str]:
        threads = max(1, int(self.settings.get("asr.cpu_threads") or 6))
        # Профиль под GTX 1650 Ti 4 ГБ: контекст 4096, один слот, batch 512, KV f16 (по умолчанию),
        # Flash Attention auto. Слои на GPU подбирает --fit с запасом 128 МиБ — рядом живёт GigaAM.
        cmd = [str(self.server_path(backend)), "-m", str(self.model_path()),
               "--host", "127.0.0.1", "--port", str(port),
               "-c", "4096", "-np", "1", "-b", "512", "-ub", "512", "-t", str(threads), "-fa", "auto",
               "--no-mmproj", "--no-webui", "--reasoning-budget", "0",
               "--chat-template-kwargs", '{"enable_thinking":false}']
        cmd += ["-ngl", "0"] if backend == "cpu" else ["-fitt", "128"]
        if backend == "vulkan":
            device = self._vulkan_nvidia(backend)
            if device:
                cmd += ["-dev", device]
        return cmd

    def _env(self, backend: str) -> dict:
        env = dict(os.environ)
        lib = str(self.engine_dir(backend))
        env["LD_LIBRARY_PATH"] = lib + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        # Код для Turing (sm_75) компилируется драйвером один раз; кэш должен вместить его целиком.
        env.setdefault("CUDA_CACHE_MAXSIZE", str(4 << 30))
        return env

    def _vulkan_nvidia(self, backend: str) -> str:
        """В ноутбуке два GPU (AMD + NVIDIA): для Vulkan явно выбираем NVIDIA."""
        try:
            out = subprocess.run([str(self.server_path(backend)), "--list-devices"], capture_output=True,
                                 text=True, timeout=20, env=self._env(backend)).stdout
        except Exception:  # noqa: BLE001
            return ""
        for line in out.splitlines():
            match = re.match(r"\s*(Vulkan\d+):\s*(.*)", line)
            if match and re.search(r"nvidia|geforce|rtx|gtx", match.group(2), re.IGNORECASE):
                return match.group(1)
        return ""

    def start(self, timeout: float = 600.0) -> bool:
        with self._lock:
            if self.ready:
                return True
            self._terminate()
            backend = self.wanted_backend()
            order = [backend] + [b for b in ("cpu",) if b != backend]
            for candidate in order:
                if not self.installed(candidate):
                    try:
                        self._set("installing", "Готовлю ИИ…")
                        self.install(candidate)
                    except Exception as exc:  # noqa: BLE001
                        self._set("error", f"Не удалось скачать ИИ: {exc}")
                        return False
                if self._launch(candidate, timeout):
                    return True
                if candidate == "cuda":
                    try:
                        if self._download_cudart(candidate) and self._launch(candidate, timeout):
                            return True
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Рантайм CUDA не скачался: %s", exc)
                log.warning("Не удалось запустить llama-server (%s), пробую следующий вариант", candidate)
            self._set("error", "ИИ не запустился — подробности в журнале llama-server.log")
            return False

    def _launch(self, backend: str, timeout: float) -> bool:
        port = free_port()
        SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        logf = open(SERVER_LOG, "ab")
        cmd = self._command(backend, port)
        log.info("Запуск: %s", " ".join(cmd))
        self._set("starting", "Запускаю ИИ…")
        try:
            proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=self._env(backend), preexec_fn=_set_pdeathsig)
        except OSError as exc:
            log.error("llama-server не стартовал: %s", exc)
            return False
        finally:
            logf.close()
        self.proc, self.port, self.backend = proc, port, backend
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if proc.poll() is not None:
                return False
            try:
                with _open(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        break
            except Exception:  # noqa: BLE001
                pass
            if time.monotonic() - started > 15:
                self._set("starting", "Первый запуск: видеокарта готовит ИИ — до пары минут, один раз…")
            time.sleep(0.25)
        else:
            self._terminate()
            return False
        label = {"cuda": "видеокарта (CUDA)", "vulkan": "видеокарта (Vulkan)", "cpu": "процессор"}[backend]
        self._set("ready", f"Qwen3.5-0.8B · {label}")
        return True

    def start_async(self) -> None:
        """Запуск в потоке-надзирателе: он живёт, пока работает сервер.

        Важно: PR_SET_PDEATHSIG привязан к потоку, создавшему процесс, — если бы поток
        запуска завершался, ядро сразу останавливало бы сервер."""
        thread = self._thread
        if thread and thread.is_alive():
            if self.ready or self.state in ("installing", "starting"):
                return
            thread.join(2)
        self._stopping = False
        self._thread = threading.Thread(target=self._supervise, name="llm-supervisor", daemon=True)
        self._thread.start()

    def _supervise(self) -> None:
        restarts = 0
        while not self._stopping:
            if not self.start():
                return
            proc = self.proc
            while proc is not None and proc.poll() is None and not self._stopping:
                time.sleep(0.5)
            if self._stopping or restarts >= 1:
                if not self._stopping:
                    self._set("error", "ИИ неожиданно остановился — подробности в llama-server.log")
                return
            restarts += 1
            log.warning("llama-server завершился (код %s), перезапускаю", proc.returncode if proc else None)

    def _terminate(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def stop(self) -> None:
        self._stopping = True
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(2)
        if self.state in ("ready", "starting"):
            self._set("stopped" if self.installed() else "absent", "")


# ---------------------------------------------------------------- Ollama
MODELFILE = """FROM {gguf}

PARAMETER num_ctx 4096
PARAMETER num_batch 512
PARAMETER num_thread 6

PARAMETER temperature 1.0
PARAMETER top_p 1.0
PARAMETER top_k 20
PARAMETER min_p 0.0
PARAMETER presence_penalty 2.0
PARAMETER repeat_penalty 1.0

SYSTEM \"\"\"Отвечай на языке пользователя. По умолчанию отвечай по-русски. Пиши грамотно и естественно. \
Строго соблюдай заданный формат, число пунктов и ограничения длины. Если просят только JSON, выводи чистый JSON \
без Markdown.\"\"\"
"""


def ollama_setup(settings: Settings, name: str = "qwen3.5:0.8b-local") -> int:
    """Использовать Ollama: готовую модель Qwen3.5-0.8B или создать её из GGUF на диске. 0 — получилось."""
    from .corrector import ollama_models, pick_qwen_small
    url = settings.get("llm.ollama_url") or "http://127.0.0.1:11434"
    models = ollama_models(url)
    if not models and not shutil.which("ollama"):
        print("Ollama не найдена.")
        return 2
    found = pick_qwen_small(models)
    if not found:
        gguf = find_local_gguf(settings.get("llm.builtin_quant") or "Q4_K_M")
        if gguf is None or not shutil.which("ollama"):
            print("В Ollama нет Qwen3.5-0.8B, и GGUF-файл на диске не найден.")
            return 3
        modelfile = LLM_DIR / "Modelfile"
        modelfile.parent.mkdir(parents=True, exist_ok=True)
        modelfile.write_text(MODELFILE.format(gguf=gguf), encoding="utf-8")
        print(f"Создаю модель {name} из {gguf} …")
        if subprocess.run(["ollama", "create", name, "-f", str(modelfile)]).returncode != 0:
            return 4
        found = name
    settings.set("llm.provider", "ollama")
    settings.set("llm.ollama_model", found)
    settings.set("llm.correct", True)
    settings.set("llm.enabled", True)
    print(f"Готово: Aqua использует Ollama · {found}")
    return 0


def deepseek_setup(settings: Settings, key: Optional[str] = None) -> int:
    """Сохранить ключ DeepSeek, включить улучшение текста и проверить ключ. 0 — работает."""
    from . import llm as cloud
    if key is None:
        import getpass
        try:
            key = getpass.getpass("Ключ DeepSeek API (ввод не виден, Enter — пропустить): ")
        except (EOFError, KeyboardInterrupt):
            key = ""
    key = (key or "").strip()
    if not key:
        print("Ключ не указан — его можно вставить позже: Настройки → Дополнительно → ИИ-помощник.")
        return 6
    settings.set("llm.deepseek_key", key)
    settings.set("llm.cloud", True)
    settings.set("llm.correct", True)
    settings.set("llm.enabled", True)
    router = cloud.Router(settings)
    provider = router.cloud()
    try:
        t0 = time.perf_counter()
        answer = cloud.request(provider, [{"role": "user", "content": "Ответь одним словом: готово"}],
                               max_tokens=10, timeout_s=20)
        print(f"Готово: DeepSeek ответил за {(time.perf_counter() - t0) * 1000:.0f} мс («{answer[:20]}»).")
        return 0
    except cloud.LLMError as exc:
        print(f"Ключ сохранён, но проверка не прошла: {cloud.explain(exc)} ({str(exc)[:120]})")
        return 5


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m aqualinux.localllm")
    sub = parser.add_subparsers(dest="cmd", required=True)
    inst = sub.add_parser("install", help="скачать движок и модель")
    inst.add_argument("--enable", action="store_true", help="сразу включить улучшение текста")
    inst.add_argument("--backend", choices=["auto", "cuda", "vulkan", "cpu"], default=None)
    sub.add_parser("status")
    sub.add_parser("configured", help="код 0 — запасная локальная модель уже готова")
    sub.add_parser("ollama-setup", help="использовать Ollama (создать qwen3.5:0.8b-local из найденного GGUF)")
    ds = sub.add_parser("deepseek", help="вставить ключ DeepSeek API и проверить его")
    ds.add_argument("--key", default=None, help="ключ (если не указан — спросить, ввод не виден)")
    test = sub.add_parser("test", help="запустить сервер и исправить пример")
    test.add_argument("text", nargs="?", default="скинь мне пожалуйста ссылку на гит хаб репозиторий "
                                                 "я хотел спросить на счёт встречи в пятницу")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")   # прогресс печатает show()
    settings = Settings()

    def show(state, message, progress):
        bar = f" [{progress * 100:5.1f}%]" if progress >= 0 else ""
        print(f"\r{message}{bar}".ljust(90), end="" if state == "installing" and progress >= 0 else "\n",
              flush=True)

    llm = LocalLLM(settings, show)
    if args.cmd == "install":
        if args.backend:
            settings.set("llm.builtin_backend", args.backend)
        llm.install()
        print("\nГотово:", llm.engine_dir(llm.wanted_backend()), llm.model_path())
        if args.enable:
            settings.set("llm.provider", "builtin")
            settings.set("llm.correct", True)
            settings.set("llm.enabled", True)
            print("Улучшение текста включено.")
        return 0
    if args.cmd == "ollama-setup":
        return ollama_setup(settings)
    if args.cmd == "configured":
        has_key = bool((settings.get("llm.deepseek_key") or "").strip())
        provider = settings.get("llm.provider") or "builtin"
        local_ok = (provider == "builtin" and llm.installed()) or provider == "ollama" or \
            (provider == "openai" and bool(settings.get("llm.base_url")))
        print("DeepSeek:", "ключ есть" if has_key else "нет ключа",
              "| запасная:", {"builtin": "Qwen3.5 (llama.cpp)", "ollama": "Ollama", "openai": "свой сервер"}
              .get(provider, provider) + (" готова" if local_ok else " не установлена"))
        return 0 if local_ok else 1
    if args.cmd == "deepseek":
        return deepseek_setup(settings, args.key)
    if args.cmd == "status":
        print("Сборка:", llm.wanted_backend(), "установлена" if llm.installed() else "не установлена")
        print("Модель:", llm.model_path() or "нет")
        return 0
    if args.cmd == "test":
        from .corrector import Corrector
        if not llm.start():
            return 1
        corrector = Corrector(settings, llm)
        for attempt in range(3):
            t0 = time.perf_counter()
            out = corrector.correct_text(args.text)
            print(f"[{(time.perf_counter() - t0) * 1000:.0f} мс] {out}")
        llm.stop()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
