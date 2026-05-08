#!/bin/bash

# Simple script to run Artery using cmake build target
# Usage: run_artery_simple.sh

ARTERY_DIR="${HOME}/Workspace/repos/artery"

# Check if Artery directory exists
if [ ! -d "$ARTERY_DIR" ]; then
    echo "Error: Artery directory not found: $ARTERY_DIR"
    exit 1
fi

# Check if build directory exists
if [ ! -d "$ARTERY_DIR/build" ]; then
    echo "Error: Artery build directory not found: $ARTERY_DIR/build"
    echo "Please run cmake to configure Artery first"
    exit 1
fi
# Kill any existing Artery processes
echo "🧹 Cleaning up existing Artery processes..."
pkill -f "opp_run.*integrated-simulator" || true
pkill -f "cmake.*run_integrated-simulator" || true
sleep 2

# Configuration
MAX_WAIT="${MAX_WAIT:-120}"  # 2 minutes max wait for SUMO service
CHECK_INTERVAL="${CHECK_INTERVAL:-2}"
SUMO_PORT="${SUMO_PORT:-8813}"

echo "========================================="
echo "Artery Launcher"
echo "========================================="
echo "Waiting for SUMO service to be online..."
echo "Max Wait: ${MAX_WAIT}s"
echo "========================================="
echo ""

# Wait for SUMO PM2 service to be online
echo "⏳ Waiting for SUMO service to be ready..."
WAITED=0

while true; do
    # Check if we've exceeded max wait time
    if [ $WAITED -ge $MAX_WAIT ]; then
        echo "❌ Error: SUMO service did not start after ${MAX_WAIT}s"
        echo "   Check logs: pm2 logs sumo-server"
        exit 1
    fi

    # Check PM2 daemon health
    if ! pm2 ping >/dev/null 2>&1; then
        echo "⚠️  PM2 daemon not responding — waiting..."
        sleep $CHECK_INTERVAL
        WAITED=$((WAITED + CHECK_INTERVAL))
        continue
    fi

    # Fetch and normalize sumo-server status (no jq)
    STATUS=$(pm2 describe sumo-server 2>/dev/null \
        | grep -m1 status \
        | sed -r 's/\x1B\[[0-9;]*[A-Za-z]//g' \
        | awk '{print $4}' \
        | tr -d '[:space:]|')
    STATUS=${STATUS:-not_found}
    echo "   SUMO status: ${STATUS} (${WAITED}s elapsed)"

    case "$STATUS" in
        "online")
            echo ""
            echo "✅ SUMO service is online"
            break
            ;;
        "launching"|"stopping")
            # Still starting/stopping, keep waiting
            ;;
        "stopped"|"errored")
            echo "❌ Error: SUMO service is ${STATUS}"
            echo "   Check logs: pm2 logs sumo-server"
            exit 1
            ;;
        "not_found")
            echo "⚠️  Warning: sumo-server process not found in PM2"
            echo "   Check if SUMO is running manually on port $SUMO_PORT..."
            break

            ;;
    esac

    sleep $CHECK_INTERVAL
    WAITED=$((WAITED + CHECK_INTERVAL))
done

# Additional wait to ensure SUMO is fully initialized and ready for connections
echo "⏳ Waiting for SUMO to stabilize (5s)..."
sleep 5

echo ""
echo "🚀 Starting Artery simulation (without GUI)..."
echo "   Working directory: $ARTERY_DIR"
echo "   Mode: Command-line (Cmdenv)"
echo ""

cd "$ARTERY_DIR"

# Set RUN_FLAGS in CMake cache if not already set
if ! grep -q 'RUN_FLAGS:STRING=-u Cmdenv' build/CMakeCache.txt 2>/dev/null; then
    echo "   Configuring CMake with RUN_FLAGS..."
    cmake -DRUN_FLAGS="-u Cmdenv" build
fi

cmake --build build --target run_integrated-simulator

EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "❌ Artery exited with error code: $EXIT_CODE"
    exit $EXIT_CODE
fi

echo ""
echo "✅ Artery simulation completed"
