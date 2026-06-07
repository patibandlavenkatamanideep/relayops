#!/usr/bin/env sh
set -eu

: "${PORT:=8501}"

echo "Starting RelayOps Streamlit on 0.0.0.0:${PORT}"

exec python -m streamlit run src/ui/app.py \
  --server.address=0.0.0.0 \
  --server.port="${PORT}" \
  --server.headless=true \
  --browser.gatherUsageStats=false
