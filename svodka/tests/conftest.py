import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Все каталоги приложения — во временной папке: тесты не трогают настоящие данные.
_TMP = Path(tempfile.mkdtemp(prefix="svodka-test-home-"))
for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                 ("XDG_STATE_HOME", "state")):
    os.environ[var] = str(_TMP / sub)
os.environ["HOME"] = str(_TMP / "home")          # таймер systemd и автозапуск — тоже во временной папке
(_TMP / "home").mkdir(parents=True, exist_ok=True)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")

FAKE_CLAUDE = ROOT / "tests" / "fakes" / "claude"
