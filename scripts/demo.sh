#!/bin/zsh
# Start the dispatcher and two mock burrows on different model families. Ctrl-C stops all three.
# No API key needed: the mock provider stands in for the AI model.
cd "$(dirname "$0")/.."
trap 'kill 0' EXIT
.venv/bin/uvicorn server.main:app --port 8000 &
sleep 2
.venv/bin/python -m worker --name demo-a --providers mock --mock-family mock-a &
.venv/bin/python -m worker --name demo-b --providers mock --mock-family mock-b --mock-rework 0.3 &
echo "Open http://localhost:8000 (site) and http://localhost:8000/app (orders)."
wait
