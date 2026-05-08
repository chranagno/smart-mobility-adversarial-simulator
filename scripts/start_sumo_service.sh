#!/bin/bash

# SUMO Service Launcher Script
# Starts SUMO in server mode with TraCI

set -e

# Configuration
SUMO_CFG="${SUMO_CFG:-}"
SUMO_PORT="${SUMO_PORT:-8813}"
NUM_CLIENTS="${NUM_CLIENTS:-2}"
SUMO_GUI="${SUMO_GUI:-false}"
SUMO_HOME="${SUMO_HOME:-/usr/share/sumo}"
MAX_WAIT="${MAX_WAIT:-30}"
STARTUP_GRACE="${STARTUP_GRACE:-5}"

echo "========================================="
echo "SUMO Service Launcher"
echo "========================================="
echo "SUMO Home:    $SUMO_HOME"
echo "Config:       $SUMO_CFG"
echo "Port:         $SUMO_PORT"
echo "Num Clients:  $NUM_CLIENTS"
echo "GUI Mode:     $SUMO_GUI"
echo "========================================="
echo ""

# Validate SUMO_HOME
if [ ! -d "$SUMO_HOME" ]; then
    echo "❌ Error: SUMO_HOME directory not found: $SUMO_HOME"
    exit 1
fi

# Validate SUMO config file
if [ -z "$SUMO_CFG" ]; then
    echo "❌ Error: SUMO_CFG not specified"
    exit 1
fi

if [ ! -f "$SUMO_CFG" ]; then
    echo "❌ Error: SUMO config file not found: $SUMO_CFG"
    exit 1
fi

# Check if SUMO is already running on this port by checking processes
if ps aux | grep -v grep | grep -q "sumo.*--remote-port $SUMO_PORT"; then
    echo "⚠️  Warning: SUMO process already running on port $SUMO_PORT"
    echo "   Attempting to clean up existing SUMO processes..."
    pkill -f "sumo.*--remote-port $SUMO_PORT" || true
    sleep 2

    # Check again
    if ps aux | grep -v grep | grep -q "sumo.*--remote-port $SUMO_PORT"; then
        echo "❌ Error: SUMO process still running on port $SUMO_PORT after cleanup"
        exit 1
    fi
    echo "✅ Cleanup successful"
fi

# Determine SUMO executable
if [ "$SUMO_GUI" = "true" ]; then
    SUMO_CMD="sumo-gui"
    echo "🖥️  Starting SUMO with GUI..."
else
    SUMO_CMD="sumo"
    echo "🚀 Starting SUMO (headless mode)..."
fi

# Check if SUMO executable exists
if ! command -v $SUMO_CMD &> /dev/null; then
    echo "❌ Error: $SUMO_CMD not found in PATH"
    echo "   Make sure SUMO is installed and SUMO_HOME is set correctly"
    exit 1
fi

# Start SUMO
echo ""
echo "Starting: $SUMO_CMD -c $SUMO_CFG --remote-port $SUMO_PORT --num-clients $NUM_CLIENTS"
echo ""

$SUMO_CMD \
    -c "$SUMO_CFG" \
    --remote-port $SUMO_PORT \
    --num-clients $NUM_CLIENTS \
    2>&1 &

SUMO_PID=$!
echo "SUMO started with PID: $SUMO_PID"

# Do not probe the TraCI socket directly here: a connect/disconnect can cause
# SUMO to report "peer shutdown" and terminate. Just give the process a short
# startup grace period and verify it stays alive.
echo "⏳ Waiting ${STARTUP_GRACE}s for SUMO to initialize..."
WAITED=0
while [ "$WAITED" -lt "$STARTUP_GRACE" ]; do
    if ! kill -0 $SUMO_PID 2>/dev/null; then
        echo "❌ Error: SUMO process died unexpectedly during startup"
        wait $SUMO_PID || true
        exit 1
    fi
    sleep 1
    WAITED=$((WAITED + 1))
    echo -n "."
done

echo ""
echo "✅ SUMO process is still alive after startup grace period"
echo "   PID: $SUMO_PID"
echo ""

# Keep the script running (for PM2)
wait $SUMO_PID

EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "❌ SUMO exited with error code: $EXIT_CODE"
    exit $EXIT_CODE
fi

echo ""
echo "✅ SUMO simulation completed"
