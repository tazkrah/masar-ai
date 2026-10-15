#!/bin/bash
# تشغيل مسار محلياً. أول مرة يجهّز البيئة (دقيقة أو دقيقتين).
set -e
cd "$(dirname "$0")"
VENV=local.nosync/venv
if [ ! -x "$VENV/bin/streamlit" ]; then
  echo "تجهيز البيئة لأول مرة..."
  mkdir -p local.nosync
  PY=$(command -v python3.12 || command -v python3.11 || command -v python3.10 || true)
  if [ -z "$PY" ]; then echo "نحتاج Python 3.10 أو أحدث (brew install python@3.12)"; exit 1; fi
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r requirements.txt
fi
[ -f .env ] || { cp .env.example .env; echo "انعمل ملف .env — حط مفتاح الموديل بيه."; }
case "$1" in
  test) exec "$VENV/bin/python" -m unittest discover tests ;;
  eval) shift; exec "$VENV/bin/python" scripts/evaluate.py "$@" ;;
  generate) shift; exec "$VENV/bin/python" scripts/generate_llm_dataset.py "$@" ;;
  *)    exec "$VENV/bin/streamlit" run app.py --server.port "${PORT:-8501}" ;;
esac
