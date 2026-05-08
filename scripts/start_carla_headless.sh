#!/bin/bash

# Headless Carla Launcher Script
# Starts Carla simulator in offscreen rendering mode (no GUI)

set -e

# Default values
CARLA_PATH="/home/chranagno/Workspace/repos/carla/Dist/CARLA_Shipping_0.9.15-dirty/LinuxNoEditor"
# CARLA_PATH="${CARLA_PATH:-/opt/carla-simulator}"
CARLA_EXECUTABLE="${CARLA_EXECUTABLE:-CarlaUE4.sh}"
CARLA_PORT="${CARLA_PORT:-2000}"
CARLA_QUALITY="${CARLA_QUALITY:-Low}"
TIMEOUT="${TIMEOUT:-60}"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --path)
            CARLA_PATH="$2"
            shift 2
            ;;
        --port)
            CARLA_PORT="$2"
            shift 2
            ;;
        --quality)
            CARLA_QUALITY="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--path PATH] [--port PORT] [--quality Low|Medium|High] [--timeout SECONDS]"
            exit 1
            ;;
    esac
done


CARLA_FULL_PATH="${CARLA_PATH}/${CARLA_EXECUTABLE}"

# Check if Carla executable exists
if [ ! -f "$CARLA_FULL_PATH" ]; then
    echo "Error: Carla executable not found at: $CARLA_FULL_PATH"
    echo "Please set CARLA_PATH environment variable or use --path argument"
    exit 1
fi

# Check if Carla is already running
if nc -z localhost "$CARLA_PORT" 2>/dev/null; then
    echo "Carla is already running on port $CARLA_PORT"
    exit 0
fi

echo "========================================="
echo "Starting Carla (Headless Mode)"
echo "========================================="
echo "Path:    $CARLA_FULL_PATH"
echo "Port:    $CARLA_PORT"
echo "Quality: $CARLA_QUALITY"
echo "========================================="

# Start Carla in headless mode
cd "$CARLA_PATH"

"$CARLA_FULL_PATH" \
    -RenderOffScreen \
    -carla-rpc-port=$CARLA_PORT \
    -quality-level=$CARLA_QUALITY \
    -nosound \
    -windowed \
    -ResX=800 -ResY=600 \
    2>&1 &

CARLA_PID=$!
echo "Carla started with PID: $CARLA_PID"

# Wait for Carla to be ready
echo "Waiting for Carla to be ready (timeout: ${TIMEOUT}s)..."
START_TIME=$(date +%s)

while ! nc -z localhost "$CARLA_PORT" 2>/dev/null; do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))

    if [ $ELAPSED -ge $TIMEOUT ]; then
        echo "Error: Carla failed to start within ${TIMEOUT} seconds"
        kill $CARLA_PID 2>/dev/null || true
        exit 1
    fi

    if ! kill -0 $CARLA_PID 2>/dev/null; then
        echo "Error: Carla process died unexpectedly"
        exit 1
    fi

    sleep 1
    echo -n "."
done

echo ""
echo "Carla is ready on port $CARLA_PORT"
echo "PID: $CARLA_PID"

# Keep the script running (for PM2)
wait $CARLA_PID
