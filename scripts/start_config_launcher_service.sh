#!/bin/bash

# Start Config Launcher Service
# This service exposes an API to configure Carla

set -e

# Configuration from environment
CARLA_HOST="${CARLA_HOST:-127.0.0.1}"
CARLA_PORT="${CARLA_PORT:-2000}"
SERVICE_HOST="${CONFIG_LAUNCHER_HOST:-127.0.0.1}"
SERVICE_PORT="${CONFIG_LAUNCHER_PORT:-5001}"
PYTHON_SCRIPTS_DIR="${PYTHON_SCRIPTS_DIR:-$(dirname "$0")/../src}"
CARLA_HOME="${CARLA_HOME:-}"

echo "========================================="
echo "Starting Config Launcher Service"
echo "========================================="
echo "Service: http://${SERVICE_HOST}:${SERVICE_PORT}"
echo "Carla: ${CARLA_HOST}:${CARLA_PORT}"
echo "========================================="
echo ""

cleanup_stale_config_launcher() {
    local pids
    pids="$(pgrep -f "python3 .*config_launcher_service.py|python .*config_launcher_service.py" || true)"

    if [ -z "$pids" ]; then
        return 0
    fi

    echo "Found existing config-launcher process(es): $pids"
    echo "Stopping stale config-launcher instance(s) before restart..."
    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 1

    local remaining
    remaining="$(pgrep -f "python3 .*config_launcher_service.py|python .*config_launcher_service.py" || true)"
    if [ -n "$remaining" ]; then
        echo "Config-launcher still running after SIGTERM, forcing shutdown: $remaining"
        echo "$remaining" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

port_in_use() {
    python3 - "$SERVICE_HOST" "$SERVICE_PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(1)
    sys.exit(0 if sock.connect_ex((host, port)) == 0 else 1)
PY
}

cleanup_stale_config_launcher

if port_in_use; then
    echo "Port ${SERVICE_PORT} is still in use after cleanup."
    echo "Another program is listening on ${SERVICE_HOST}:${SERVICE_PORT}."
    echo "Stop that process or set CONFIG_LAUNCHER_PORT to a different port."
    exit 1
fi

# Change to Python source directory
cd "$PYTHON_SCRIPTS_DIR"

# Export environment variables for the service
export CARLA_HOST
export CARLA_PORT
export CARLA_HOME
export PYTHON_SCRIPTS_DIR
export PYTHONPATH="${CARLA_HOME}:${CARLA_HOME}/carla/dist/carla-0.9.15-py3.7-linux-x86_64.egg${PYTHONPATH:+:$PYTHONPATH}"

# Start the service
python3 config_launcher_service.py \
    --host "$SERVICE_HOST" \
    --port "$SERVICE_PORT" \
    --carla-host "$CARLA_HOST" \
    --carla-port "$CARLA_PORT"
