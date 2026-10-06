"""Движок распознавания GigaAM v3 E2E RNNT для диктовки.

Работает в отдельном потоке. Весь код PyTorch (загрузка, CUDA Graphs, VAD)
выполняется только в нём, интерфейс получает результаты через колбэки.

Псевдопотоковый режим: пока человек говорит, Silero VAD ищет паузы и уже
законченные фрагменты (6–18 с) распознаются сразу. После отпускания клавиши
остаётся распознать только хвост — поэтому текст появляется почти мгновенно
даже после длинной диктовки. Модель полноконтекстная, поэтому каждый фрагмент
распознаётся целиком, а «живой» текст — это повторное распознавание хвоста.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ..config import CACHE_DIR, MODEL_FILES, MODEL_NAME, MODELS_DIR, PROJECT_ROOT, Settings, find_model_dir

log = logging.getLogger(__name__)

SR = 16000
FRAME = 512                 # кадр Silero VAD (32 мс)
MIN_COMMIT_S = 6.0          # раньше этой длины фрагмент не фиксируем
SOFT_MAX_S = 14.0           # после этого фиксируем на любой паузе от 0,2 с
HARD_MAX_S = 18.0           # жёсткий предел фрагмента (граф декодера рассчитан на 25 с)
PAUSE_COMMIT_S = 0.40       # пауза, по которой фиксируем фрагмент
SPEECH_THRESHOLD = 0.5
MIN_SPEECH_S = 0.18         # меньше речи во фрагменте — считаем тишиной


@dataclass
class Session:
    sid: int
    started: float = field(default_factory=time.monotonic)
    audio: list = field(default_factory=list)       # список массивов float32
    total: int = 0                                  # всего сэмплов
    vad_pending: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    probs: list = field(default_factory=list)       # вероятность речи на кадр
    commit_pos: int = 0                             # сэмпл, с которого не зафиксировано
    texts: list = field(default_factory=list)       # тексты зафиксированных фрагментов
    partial: str = ""
    last_preview_at: float = 0.0
    last_preview_total: int = 0
    asr_seconds: float = 0.0
    stopped: bool = False
    preview: bool = False
    _flat: Optional[np.ndarray] = None

    def flat(self) -> np.ndarray:
        if self._flat is None or self._flat.size != self.total:
            self._flat = np.concatenate(self.audio) if self.audio else np.zeros(0, np.float32)
            self.audio = [self._flat]
        return self._flat


class ASREngine:
    """Потокобезопасная обёртка над моделью.

    Колбэки вызываются из потока движка:
      on_status(state: str, message: str)   state: loading|ready|error|unloaded|downloading
      on_partial(sid, text)
      on_final(sid, text, info: dict)
    """

    def __init__(self, settings: Settings,
                 on_status: Callable[[str, str], None],
                 on_partial: Callable[[int, str], None],
                 on_final: Callable[[int, str, dict], None],
                 on_chunk: Optional[Callable[[int, int, str], None]] = None):
        self.settings = settings
        self.on_chunk = on_chunk
        self.on_status = on_status
        self.on_partial = on_partial
        self.on_final = on_final
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="asr-engine", daemon=True)
        self._session: Optional[Session] = None
        self.model = None
        self.vad = None
        self.graph = None
        self.torch = None
        self.device = "cpu"
        self.precision = "fp32"
        self.state = "unloaded"
        self.last_used = time.monotonic()
        self.device_label = ""
        self.force_cpu_reason = ""     # почему распознавание ушло на процессор (видеопамять занята)
        self.busy_since: Optional[float] = None   # с какого момента поток занят одной операцией

    # ------------------------------------------------------------------ API
    def start(self) -> None:
        self._thread.start()
        self._q.put(("load",))

    def reload(self) -> None:
        self._q.put(("unload",))
        self._q.put(("load",))

    def alive(self) -> bool:
        """Поток распознавания жив (или ещё не запускался)."""
        return not self._thread.is_alive() and self._thread.ident is None or self._thread.is_alive()

    def health_check(self, callback) -> None:
        """Короткая проверка (после сна): callback(True/False) из потока распознавания."""
        self._q.put(("health", callback))

    def retry_gpu(self) -> None:
        """Ещё раз попробовать видеокарту (например, после выгрузки моделей Ollama)."""
        self._q.put(("retry_gpu",))

    def begin(self, sid: int, preview: bool = False) -> None:
        self._q.put(("begin", sid, preview))

    def set_preview(self, sid: int, preview: bool) -> None:
        self._q.put(("preview", sid, preview))

    def feed(self, sid: int, chunk: np.ndarray) -> None:
        self._q.put(("audio", sid, chunk))

    def finish(self, sid: int) -> None:
        self._q.put(("finish", sid))

    def cancel(self, sid: int) -> None:
        self._q.put(("cancel", sid))

    def transcribe_array(self, audio: np.ndarray, callback: Callable[[str], None]) -> None:
        """Распознать готовую запись (повтор из истории)."""
        self._q.put(("file", audio, callback))

    def shutdown(self) -> None:
        self._q.put(("quit",))

    # ---------------------------------------------------------------- поток
    def _run(self) -> None:
        while True:
            # Идёт запись — проверяем очередь часто (живой текст, фрагменты); в простое — спим
            # до команды (раз в 5 с — для выгрузки модели по таймеру): меньше пробуждений процессора.
            active = self._session is not None and not self._session.stopped
            try:
                cmd = self._q.get(timeout=0.05 if active else 5.0)
            except queue.Empty:
                cmd = None
            try:
                self.busy_since = time.monotonic()
                if cmd is not None:
                    if cmd[0] == "quit":
                        return
                    self._handle(cmd)
                    # Сначала разбираем всё, что накопилось в очереди (аудио).
                    if not self._q.empty():
                        continue
                self._tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("Ошибка движка")
                sess = self._session
                if sess is not None and cmd is not None and cmd[0] == "finish":
                    self._session = None
                    self.on_final(sess.sid, "", {"error": str(exc)})
            finally:
                self.busy_since = None

    def _handle(self, cmd: tuple) -> None:
        kind = cmd[0]
        if kind == "load":
            self._load()
        elif kind == "unload":
            self._unload()
        elif kind == "health":
            ok = True
            if self.model is not None:
                try:
                    self._transcribe_once((np.random.default_rng(1).standard_normal(SR // 2) * 1e-3)
                                          .astype(np.float32))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Проверка распознавателя не прошла: %s", exc)
                    ok = False
            cmd[1](ok)
        elif kind == "retry_gpu":
            self.force_cpu_reason = ""
            self._unload()
            self._load()
        elif kind == "begin":
            self._session = Session(cmd[1], preview=cmd[2])
            if self.vad is not None:
                self.vad.reset_states()
        elif kind == "audio":
            sess = self._session
            if sess is not None and sess.sid == cmd[1] and not sess.stopped:
                chunk = np.asarray(cmd[2], dtype=np.float32).reshape(-1)
                sess.audio.append(chunk)
                sess.total += chunk.size
                self._run_vad(sess, chunk)
        elif kind == "finish":
            sess = self._session
            if sess is not None and sess.sid == cmd[1]:
                sess.stopped = True
                self._finish(sess)
        elif kind == "preview":
            if self._session is not None and self._session.sid == cmd[1]:
                self._session.preview = cmd[2]
        elif kind == "cancel":
            if self._session is not None and self._session.sid == cmd[1]:
                self._session = None
        elif kind == "file":
            audio, callback = cmd[1], cmd[2]
            if not self._ensure_loaded():
                callback("")
                return
            callback(self._transcribe_long(np.asarray(audio, np.float32)))

    def _tick(self) -> None:
        sess = self._session
        if sess is not None and not sess.stopped and self.model is not None:
            while self._commit_ready(sess):
                pass
            self._maybe_preview(sess)
        elif sess is None and self.model is not None:
            minutes = self.settings.get("asr.unload_after_min") or 0
            if minutes and time.monotonic() - self.last_used > minutes * 60:
                self._unload()
                self.on_status("unloaded", "Модель выгружена (простой)")

    # ------------------------------------------------------------ загрузка
    def _ensure_loaded(self) -> bool:
        if self.model is None:
            self._load()
        return self.model is not None

    def _resolve_device(self, torch) -> tuple[str, str]:
        want = self.settings.get("asr.device") or "auto"
        precision = self.settings.get("asr.precision") or "auto"
        cuda_ok = torch.cuda.is_available()
        if want == "cuda" and not cuda_ok:
            log.warning("CUDA недоступна, переключаюсь на CPU")
        device = "cuda" if (want in ("auto", "cuda") and cuda_ok) else "cpu"
        if device == "cuda" and self.force_cpu_reason:
            device = "cpu"
        elif device == "cuda":
            try:
                free, _total = torch.cuda.mem_get_info()
                free_mb = int(free / 2 ** 20)
            except Exception:  # noqa: BLE001
                free_mb = None
            from ..gpu import ASR_NEED_MB, describe
            if free_mb is not None and free_mb < ASR_NEED_MB:
                # Видеопамять заняли (Ollama, игра…): работаем на процессоре и честно говорим почему.
                self.force_cpu_reason = describe(free_mb)
                log.warning("%s — распознавание на CPU", self.force_cpu_reason)
                device = "cpu"
        if precision == "auto":
            from .. import perf
            precision = "int8" if device == "cuda" else perf.cpu_precision()
        if device == "cpu" and precision == "fp16":
            precision = "fp32"
        return device, precision

    def _model_paths(self) -> tuple[Path, Path]:
        directory = find_model_dir(self.settings)
        if directory is None:
            directory = Path(self.settings.get("asr.model_dir") or MODELS_DIR).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            self.on_status("downloading", "Скачиваю веса GigaAM v3 E2E RNNT (~0,9 ГБ)…")
            from gigaam import _download_model, _download_tokenizer
            _download_model(MODEL_NAME, str(directory))
            _download_tokenizer(MODEL_NAME, str(directory))
        return directory / MODEL_FILES[0], directory / MODEL_FILES[1]

    def _verify_checksum(self, ckpt: Path) -> None:
        """MD5 считаем один раз и запоминаем (размер+время изменения)."""
        from gigaam import _MODEL_HASHES
        marker = CACHE_DIR / "verified.json"
        stat = ckpt.stat()
        key = f"{ckpt.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
        try:
            known = json.loads(marker.read_text())
        except (OSError, ValueError):
            known = {}
        if known.get(key) == _MODEL_HASHES[MODEL_NAME]:
            return
        digest = hashlib.md5()
        with open(ckpt, "rb") as fh:
            for block in iter(lambda: fh.read(8 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != _MODEL_HASHES[MODEL_NAME]:
            # Файл повреждён (оборванная загрузка, сбой диска): убираем его — модель скачается заново.
            # Чужой файл по ссылке не трогаем, удаляем только ссылку.
            if ckpt.is_symlink():
                ckpt.unlink()
            else:
                ckpt.rename(ckpt.with_name(ckpt.name + ".broken"))
            raise _Redownload(f"{ckpt.name} повреждён — скачиваю заново")
        known[key] = digest.hexdigest()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(known))

    def _load(self) -> None:
        if self.model is not None:
            return
        self.state = "loading"
        self.on_status("loading", "Загружаю модель распознавания…")
        started = time.perf_counter()
        try:
            vendor = str(PROJECT_ROOT / "third_party" / "gigaam")
            if vendor not in sys.path:
                sys.path.insert(0, vendor)
            import torch
            import warnings
            self.torch = torch
            from .. import perf
            torch.set_num_threads(perf.cpu_threads(self.settings))
            torch.backends.cudnn.allow_tf32 = False
            from gigaam.model import GigaAMASR

            ckpt_path, tok_path = self._model_paths()
            self._verify_checksum(ckpt_path)
            device, precision = self._resolve_device(torch)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                checkpoint = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
            cfg = checkpoint["cfg"]
            cfg.encoder.flash_attn = False
            cfg.decoding.model_path = str(tok_path)
            cfg.model_name = MODEL_NAME
            model = GigaAMASR(cfg)
            model.load_state_dict(checkpoint["state_dict"])
            del checkpoint
            model = model.eval()
            if precision == "fp16":
                model.encoder = model.encoder.half()
            if precision == "int8" and device == "cuda":
                # Квантуем ещё на CPU: на видеокарту уходят уже INT8-веса (пик памяти втрое ниже).
                from .int8_encoder import quantize_encoder
                model.encoder.float()
                quantize_encoder(model.encoder)
            try:
                model = model.to(device)
                if device == "cuda":
                    torch.cuda.empty_cache()
                    from .fast_decoder import GraphRNNT
                    self.graph = GraphRNNT(model.head, model.decoding, 1, steps=8)
                    self.device_label = torch.cuda.get_device_name(0)
            except Exception as exc:  # noqa: BLE001
                if device != "cuda" or not _is_oom(exc):
                    raise
                from ..gpu import describe
                self.force_cpu_reason = describe()
                log.warning("Нехватка видеопамяти при загрузке: %s", exc)
                self.graph = None
                model = None  # noqa: F841 — отпускаем веса на видеокарте
                self._free_cuda()
                self._load_cpu_retry()
                return
            if precision == "int8" and device == "cpu":
                model.encoder = torch.ao.quantization.quantize_dynamic(
                    model.encoder, {torch.nn.Linear}, dtype=torch.qint8)
            if device == "cpu":
                self.graph = None
                self.device_label = "CPU"
            self.model, self.device, self.precision = model, device, precision

            from silero_vad import load_silero_vad
            self.vad = load_silero_vad()

            if self.settings.get("asr.warmup", True):
                noise = (np.random.default_rng(0).standard_normal(SR) * 1e-3).astype(np.float32)
                self._transcribe(noise)
                self._transcribe(noise[: SR // 2])
            self.state = "ready"
            self.last_used = time.monotonic()
            took = time.perf_counter() - started
            if device == "cpu" and self.force_cpu_reason:
                self.on_status("degraded", self.force_cpu_reason)
            else:
                self.on_status("ready", f"{self.device_label} · {precision.upper()} · загрузка {took:.1f} с")
            log.info("Модель загружена: %s %s за %.1f с", device, precision, took)
        except _Redownload as exc:
            log.warning("%s", exc)
            self.model = None
            if not getattr(self, "_redownloaded", False):
                self._redownloaded = True
                self._load()
                return
            self.state = "error"
            self.on_status("error", f"Ошибка загрузки модели: {exc}")
        except Exception as exc:  # noqa: BLE001
            log.exception("Не удалось загрузить модель")
            self.model = None
            self.graph = None
            if getattr(self, "device", "cpu") == "cuda" or (self.torch is not None and not self.force_cpu_reason
                                                            and self.settings.get("asr.device") != "cpu"
                                                            and self.torch.cuda.is_available()):
                # Видеокарта подвела (драйвер, CUDA) — сами переходим на процессор.
                self.force_cpu_reason = f"Видеокарта недоступна ({str(exc)[:80]}) — распознаю на процессоре."
                self._free_cuda()
                self.device = "cpu"
                self._load()
                return
            self.state = "error"
            self.on_status("error", f"Ошибка загрузки модели: {exc}")

    def _unload(self) -> None:
        if self.model is None:
            return
        self.model = None
        self.graph = None
        self.state = "unloaded"
        self._free_cuda()

    def _free_cuda(self) -> None:
        if self.torch is not None and self.torch.cuda.is_available():
            import gc
            gc.collect()
            try:
                self.torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass

    def _load_cpu_retry(self) -> None:
        """Загрузить модель на процессор после нехватки видеопамяти."""
        self.model = None
        self.graph = None
        self._free_cuda()
        self._load()

    def _to_cpu_after_oom(self, exc: Exception) -> None:
        from ..gpu import describe
        self.force_cpu_reason = describe()
        log.warning("Нехватка видеопамяти во время распознавания (%s) — перехожу на CPU", exc)
        self.model = None
        self.graph = None
        self._load_cpu_retry()

    # -------------------------------------------------------- распознавание
    def _transcribe(self, wav: np.ndarray) -> str:
        try:
            return self._transcribe_once(wav)
        except Exception as exc:  # noqa: BLE001
            if self.device != "cuda" or not _is_oom(exc):
                raise
            # Кто-то занял видеопамять уже после загрузки (например, Ollama подняла модель).
            self._to_cpu_after_oom(exc)
            if self.model is None:
                raise
            return self._transcribe_once(wav)

    def _transcribe_once(self, wav: np.ndarray) -> str:
        torch = self.torch
        if wav.size < FRAME:
            return ""
        t0 = time.perf_counter()
        with torch.inference_mode():
            x = torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32)).to(self.device).unsqueeze(0)
            lengths = torch.tensor([x.shape[-1]], device=self.device)
            if self.precision == "fp16":
                encoded, enc_len = self.model.forward(x, lengths)
            else:
                # GigaAM.forward включает FP16 autocast на CUDA; для INT8/FP32 обходим его.
                features, feat_len = self.model.preprocessor(x, lengths)
                encoded, enc_len = self.model.encoder(features, feat_len)
            if self.graph is not None and encoded.shape[-1] <= self.graph.max_frames:
                decoded = self.graph.decode(encoded.float(), enc_len)
            else:
                decoded = self.model.decoding.decode(self.model.head, encoded.float(), enc_len)
        self.last_used = time.monotonic()
        text = decoded[0][0].strip()
        log.debug("ASR %.2f с аудио за %.0f мс: %s", wav.size / SR, (time.perf_counter() - t0) * 1000, text)
        return text

    def _transcribe_long(self, audio: np.ndarray) -> str:
        """Готовая запись любой длины: режем по VAD так же, как при диктовке."""
        sess = Session(-1)
        if self.vad is not None:
            self.vad.reset_states()
        sess.audio, sess.total = [audio], audio.size
        self._run_vad(sess, audio)
        sess.stopped = True
        while self._commit_ready(sess, final=False):
            pass
        tail = sess.flat()[sess.commit_pos:]
        if self._has_speech(sess, sess.commit_pos, sess.total):
            sess.texts.append(self._transcribe(tail))
        return join_texts(sess.texts)

    # ------------------------------------------------------------- VAD/сегменты
    def _run_vad(self, sess: Session, chunk: np.ndarray) -> None:
        if self.vad is None:
            # Модель ещё грузится: копим, VAD догонит позже.
            sess.vad_pending = np.concatenate([sess.vad_pending, chunk])
            return
        data = np.concatenate([sess.vad_pending, chunk]) if sess.vad_pending.size else chunk
        n = data.size // FRAME
        if n:
            torch = self.torch
            frames = torch.from_numpy(np.ascontiguousarray(data[: n * FRAME])).view(n, FRAME)
            with torch.inference_mode():
                for frame in frames:
                    sess.probs.append(float(self.vad(frame, SR)))
        sess.vad_pending = data[n * FRAME:].copy()

    def _has_speech(self, sess: Session, start: int, end: int) -> bool:
        a, b = start // FRAME, min(len(sess.probs), (end + FRAME - 1) // FRAME)
        if b <= a:
            # VAD не успел — доверяем энергии сигнала
            seg = sess.flat()[start:end]
            return seg.size > SR * 0.2 and float(np.sqrt(np.mean(seg ** 2))) > 0.004
        speech = sum(1 for p in sess.probs[a:b] if p >= SPEECH_THRESHOLD)
        return speech * FRAME / SR >= MIN_SPEECH_S

    def _find_cut(self, sess: Session, final: bool) -> Optional[int]:
        """Граница следующего фрагмента в сэмплах или None."""
        start_frame = sess.commit_pos // FRAME
        probs = sess.probs
        avail = len(probs) - start_frame
        length_s = avail * FRAME / SR
        if length_s < MIN_COMMIT_S:
            return None
        min_frames = int(MIN_COMMIT_S * SR / FRAME)
        hard_frames = int(HARD_MAX_S * SR / FRAME)
        window_end = start_frame + min(avail, hard_frames)
        # Ищем самую длинную паузу после MIN_COMMIT_S (ближе к концу — лучше).
        best = None
        run = 0
        for i in range(start_frame + min_frames, window_end):
            if probs[i] < SPEECH_THRESHOLD:
                run += 1
            else:
                if run:
                    seconds = run * FRAME / SR
                    end_i = i
                    position_s = (end_i - start_frame) * FRAME / SR
                    need = PAUSE_COMMIT_S if position_s < SOFT_MAX_S else 0.2
                    if seconds >= need and (best is None or run >= best[1] * 0.7):
                        best = (end_i - run // 2, run)
                run = 0
        # Пауза продолжается до текущего момента.
        if run and window_end == len(probs):
            seconds = run * FRAME / SR
            if seconds >= PAUSE_COMMIT_S:
                best = (window_end - run // 2, run)
        if best is not None:
            return best[0] * FRAME
        if avail >= hard_frames:
            # Длинная речь без явных пауз: самая длинная (хоть короткая) пауза после 2 с,
            # иначе — самый тихий кадр в последних 4 с.
            lo = start_frame + int(2.0 * SR / FRAME)
            hi = start_frame + hard_frames
            best_run, best_mid, run = 0, None, 0
            for i in range(lo, hi):
                if probs[i] < SPEECH_THRESHOLD:
                    run += 1
                    if run >= best_run:
                        best_run, best_mid = run, i - run // 2
                else:
                    run = 0
            if best_mid is not None and best_run >= 2:
                return best_mid * FRAME
            lo = hi - int(4 * SR / FRAME)
            audio = sess.flat()[lo * FRAME:hi * FRAME]
            energy = np.sqrt(np.mean(audio[: (hi - lo) * FRAME].reshape(hi - lo, FRAME) ** 2, axis=1))
            score = np.asarray(probs[lo:hi]) + energy / (energy.max() + 1e-9) * 0.5
            return (lo + int(np.argmin(score))) * FRAME
        return None

    def _commit_ready(self, sess: Session, final: bool = False) -> bool:
        if self.model is None:
            return False
        cut = self._find_cut(sess, final)
        if cut is None or cut <= sess.commit_pos:
            return False
        start = sess.commit_pos
        if self._has_speech(sess, start, cut):
            t0 = time.perf_counter()
            text = self._transcribe(sess.flat()[start:cut])
            sess.asr_seconds += time.perf_counter() - t0
            if text:
                sess.texts.append(text)
                if sess.sid >= 0 and self.on_chunk is not None:
                    self.on_chunk(sess.sid, len(sess.texts) - 1, text)
        sess.commit_pos = cut
        sess.partial = ""
        if sess.sid >= 0:
            self.on_partial(sess.sid, join_texts(sess.texts))
        return True

    def _maybe_preview(self, sess: Session) -> None:
        if not sess.preview:
            return
        now = time.monotonic()
        from .. import perf
        interval = max(0.25, perf.preview_interval_ms(self.settings) / 1000)
        if now - sess.last_preview_at < interval or sess.total - sess.last_preview_total < SR * 0.25:
            return
        start = sess.commit_pos
        if sess.total - start < SR * 0.4 or not self._has_speech(sess, start, sess.total):
            return
        t0 = time.perf_counter()
        sess.partial = self._transcribe(sess.flat()[start:sess.total])
        cost = time.perf_counter() - t0
        sess.last_preview_at = time.monotonic() + cost  # на медленном CPU реже
        sess.last_preview_total = sess.total
        self.on_partial(sess.sid, join_texts(sess.texts + [sess.partial]))

    def _finish(self, sess: Session) -> None:
        t0 = time.perf_counter()
        if not self._ensure_loaded():
            self._session = None
            self.on_final(sess.sid, "", {"error": "Модель не загружена"})
            return
        if sess.vad_pending.size >= FRAME or len(sess.probs) * FRAME < sess.total - FRAME:
            pending = sess.flat()[len(sess.probs) * FRAME:]
            sess.vad_pending = np.zeros(0, np.float32)
            self._run_vad(sess, pending)
        while self._commit_ready(sess, final=True):
            pass
        speech_any = self._has_speech(sess, 0, sess.total)
        if sess.total - sess.commit_pos >= FRAME and self._has_speech(sess, sess.commit_pos, sess.total):
            text = self._transcribe(sess.flat()[sess.commit_pos:])
            if text:
                sess.texts.append(text)
        final_text = join_texts(sess.texts)
        info = {
            "duration": sess.total / SR,
            "latency": time.perf_counter() - t0,
            "speech": speech_any,
            "device": self.device_label,
            "precision": self.precision,
            "audio": sess.flat(),
            "chunks": list(sess.texts),
        }
        self._session = None
        self.on_final(sess.sid, final_text, info)


class _Redownload(RuntimeError):
    pass


def _is_oom(exc: Exception) -> bool:
    text = str(exc).lower()
    return exc.__class__.__name__ == "OutOfMemoryError" or "out of memory" in text \
        or "cublas_status_alloc_failed" in text or "cudaerrormemoryallocation" in text


def join_texts(parts: list) -> str:
    out = ""
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        out = f"{out} {part}" if out else part
    return out
