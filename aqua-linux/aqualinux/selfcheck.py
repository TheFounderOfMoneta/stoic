"""Самопроверка окружения: python -m aqualinux.selfcheck [--icon PATH]."""
from __future__ import annotations

import argparse
import os
import sys

from .config import PROJECT_ROOT, Settings, find_model_dir

OK, WARN, FAIL = "\033[32m✓\033[0m", "\033[33m!\033[0m", "\033[31m✕\033[0m"


def write_icon(path: str) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841
    from .ui.theme import app_icon_pixmap
    os.makedirs(os.path.dirname(path), exist_ok=True)
    app_icon_pixmap(256).save(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--icon")
    args = parser.parse_args()
    if args.icon:
        write_icon(args.icon)
        return 0
    problems = 0
    print(f"Python {sys.version.split()[0]}  ({sys.executable})")
    try:
        import torch
        print(f"{OK} PyTorch {torch.__version__}")
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            cap = torch.cuda.get_device_capability(0)
            free, total = torch.cuda.mem_get_info()
            print(f"{OK} CUDA: {name}, sm_{cap[0]}{cap[1]}, свободно {free / 2**30:.1f} из {total / 2**30:.1f} ГиБ")
            try:
                a = torch.ones((8, 32), dtype=torch.int8, device="cuda")
                b = torch.ones((32, 8), dtype=torch.int8, device="cuda")
                torch._int_mm(a, b)
                print(f"{OK} INT8 (torch._int_mm) работает на GPU")
            except Exception as exc:  # noqa: BLE001
                print(f"{WARN} INT8 на GPU недоступен ({exc}); выберите точность FP16/FP32")
        else:
            print(f"{WARN} CUDA недоступна — распознавание пойдёт на процессоре (медленнее)")
        import torchaudio  # noqa: F401
        print(f"{OK} torchaudio {torchaudio.__version__}")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} PyTorch/torchaudio: {exc}")
        problems += 1
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "third_party" / "gigaam"))
        import gigaam  # noqa: F401
        import silero_vad  # noqa: F401
        print(f"{OK} GigaAM и Silero VAD импортируются")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} GigaAM/Silero: {exc}")
        problems += 1
    try:
        from PySide6 import __version__ as qt_version
        print(f"{OK} PySide6 {qt_version}")
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} PySide6: {exc}")
        problems += 1
    from .audio import audio_backend_error, list_input_devices
    err = audio_backend_error()
    if err:
        print(f"{FAIL} {err}")
        problems += 1
    else:
        mics = list_input_devices()
        print(f"{OK} Микрофоны: {len(mics) - 1} устройств(а)")
    if os.environ.get("DISPLAY"):
        try:
            from Xlib import display
            d = display.Display()
            missing = [ext for ext in ("RECORD", "XTEST", "XFIXES", "SHAPE") if not d.has_extension(ext)]
            if missing:
                print(f"{WARN} Нет расширений X11: {', '.join(missing)}")
            else:
                print(f"{OK} X11: RECORD, XTEST, XFIXES, SHAPE")
        except Exception as exc:  # noqa: BLE001
            print(f"{FAIL} X11: {exc}")
            problems += 1
    session = os.environ.get("XDG_SESSION_TYPE", "")
    if session == "wayland":
        print(f"{WARN} Сеанс Wayland: удержание клавиши не работает. Выберите «Ubuntu на Xorg» на экране входа.")
    found = find_model_dir(Settings())
    if found:
        print(f"{OK} Веса модели: {found}")
    else:
        print(f"{WARN} Веса модели не найдены — скачаются при первом запуске (~0,9 ГБ)")
    print("Готово." if not problems else f"Проблем: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
