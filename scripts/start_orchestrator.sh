#!/bin/bash
# Start OpenCDA Orchestrator with YAML scenario configuration
# This script extracts parameters from YAML and passes them to orchestrator.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_SCRIPTS="$PROJECT_ROOT/src"

# Get scenario YAML path from environment variable or argument
SCENARIO_YAML="${SCENARIO_YAML:-${1:-}}"

# Default data dump directory (can be overridden via DATA_DUMP_DIR env)
if [ -z "$DATA_DUMP_DIR" ]; then
    DATA_DUMP_DIR="$PYTHON_SCRIPTS/data_dumping"
fi

if [ -z "$SCENARIO_YAML" ]; then
    echo "Error: SCENARIO_YAML environment variable or argument required"
    echo "Usage: $0 <path_to_scenario_yaml>"
    exit 1
fi

# Convert relative path to absolute if needed
if [[ ! "$SCENARIO_YAML" = /* ]]; then
    SCENARIO_YAML="$PROJECT_ROOT/$SCENARIO_YAML"
fi

if [ ! -f "$SCENARIO_YAML" ]; then
    echo "Error: Scenario YAML not found: $SCENARIO_YAML"
    exit 1
fi

echo "=========================================="
echo "Starting OpenCDA Orchestrator"
echo "=========================================="
echo "Scenario YAML: $SCENARIO_YAML"
echo "=========================================="

# Change to Python source directory
cd "$PYTHON_SCRIPTS" || exit 1

# Build command arguments
# The orchestrator will extract carla-host, carla-town, sumo-cfg-file from YAML
# WebSocket is enabled by default
ARGS=(
    "--scenario-config" "$SCENARIO_YAML"
    "--enable-ws"  # Enabled by default
    "--ws-port" "${WS_PORT:-8765}"
)

# Optional: Add general config if specified
if [ -n "$GENERAL_CONFIG" ]; then
    ARGS+=("--general-config" "$GENERAL_CONFIG")
fi

# Optional: Add artery settings if specified
if [ "${START_ARTERY:-false}" = "true" ]; then
    ARGS+=("--start-artery" "--artery-control")
fi

# Optional: Enable data dumping/recording (sensors + ground truth)
if [ "${DATA_DUMP:-false}" = "true" ]; then
    echo "Data dump enabled via DATA_DUMP env"
    ARGS+=("--data-dump")
fi

# Run the orchestrator explicitly with carla conda python
export DATA_DUMP_DIR
source /home/chranagno/anaconda3/etc/profile.d/conda.sh
conda activate carla

exec python3 orchestrator.py "${ARGS[@]}"
