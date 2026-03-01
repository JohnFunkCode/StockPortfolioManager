#!/bin/bash
# Starts the Flask API and React frontend servers in the background.
# Logs are written to api.log and frontent.log in the project root.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Starting API server... (logs: api.log)"
source "$SCRIPT_DIR/.venv/bin/activate"
python -m api.app > "$SCRIPT_DIR/api.log" 2>&1 &
API_PID=$!

echo "Starting frontend server... (logs: frontend.log)"
cd "$SCRIPT_DIR/frontend"
npm run dev > "$SCRIPT_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

echo "API PID:      $API_PID"
echo "Frontend PID: $FRONTEND_PID"
echo ""
echo "API:      http://127.0.0.1:5000"
echo "Frontend: http://localhost:5173"
echo ""
echo "To stop:  kill $API_PID $FRONTEND_PID"
