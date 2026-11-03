#!/usr/bin/env bash
# Starts the Moodify backend (FastAPI, port 8235) and frontend (Streamlit, port 8501). Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")"

VENV=${VENV:-venv}
if [ ! -x "$VENV/bin/python" ]; then
  echo "No virtualenv at ./$VENV. Set it up first: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [ ! -f .env ]; then
  echo "Missing .env. Copy .env.example to .env and fill it in."
  exit 1
fi

OLLAMA_URL=$(grep -E '^OLLAMA_URL=' .env | cut -d= -f2- || true)
if ! curl -s -m 2 "${OLLAMA_URL:-http://localhost:11434}/api/tags" >/dev/null; then
  echo "Note: Ollama is not running, so smart search is off. Start it with: ollama serve"
fi

BACKEND=""
FRONTEND=""
trap 'kill $BACKEND $FRONTEND 2>/dev/null || true' EXIT

"$VENV/bin/uvicorn" backend:app --host 127.0.0.1 --port 8235 &
BACKEND=$!
echo "Waiting for the backend (loading the embedding model can take a minute)..."
until curl -s -m 2 http://127.0.0.1:8235/health >/dev/null; do
  if ! kill -0 "$BACKEND" 2>/dev/null; then
    echo "The backend failed to start, see the errors above."
    exit 1
  fi
  sleep 2
done

"$VENV/bin/streamlit" run app.py --server.headless true --server.port 8501 &
FRONTEND=$!
echo "Moodify is running at http://localhost:8501"
wait
