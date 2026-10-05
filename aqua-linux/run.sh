#!/usr/bin/env bash
# Запуск без установки ярлыков (после bash install.sh).
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$ROOT/.venv/bin/python" -m aqualinux "$@"
