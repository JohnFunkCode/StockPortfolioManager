#!/bin/bash
# Starts the Flask API and React frontend servers in the background.
# Logs are written to api.log and frontend.log in the project root.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Clean up any old processes
echo "Cleaning up old processes..."
pkill -f "python -m api.app" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
sleep 1

echo "Starting API server... (logs: api.log)"
source "$SCRIPT_DIR/.venv/bin/activate"
python -m api.app > "$SCRIPT_DIR/api.log" 2>&1 &
API_PID=$!

echo "Starting frontend server... (logs: frontend.log)"
cd "$SCRIPT_DIR/frontend"
npm run dev > "$SCRIPT_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

sleep 3

echo ""
echo "✓ Servers started"
echo "API PID:      $API_PID"
echo "Frontend PID: $FRONTEND_PID"
echo ""
echo "API:      http://127.0.0.1:5001"
echo "Frontend: http://localhost:5173"
echo ""
echo "To stop:  pkill -f 'python -m api.app' && pkill -f 'vite'"
