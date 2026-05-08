#!/bin/bash

# Simulator Startup Script
# This script starts both the frontend web interface and simulator services

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Scenario loading is opt-in. If omitted, start the simulator UI/backend only.
SCENARIO=""
BACKEND_PID=""
FRONTEND_PID=""

# Parse options
START_SERVICES=true
START_FRONTEND=true
DATA_DUMP=false

wait_for_port() {
    local host="$1"
    local port="$2"
    local label="$3"
    local max_wait="${4:-30}"

    for ((i=1; i<=max_wait; i++)); do
        if python3 - "$host" "$port" >/dev/null 2>&1 <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.create_connection((host, port), timeout=1.0):
    pass
PY
        then
            echo -e "${GREEN}✓ ${label} is ready on ${host}:${port}${NC}"
            return 0
        fi
        sleep 1
    done

    echo -e "${YELLOW}Warning: ${label} did not become ready on ${host}:${port} within ${max_wait}s${NC}"
    return 1
}

get_primary_ip() {
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
    printf '%s' "$ip"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --frontend-only)
            START_SERVICES=false
            shift
            ;;
        --services-only)
            START_FRONTEND=false
            shift
            ;;
        --data-dump|--record)
            DATA_DUMP=true
            shift
            ;;
        --scenario)
            SCENARIO="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Start the simulator without loading a scenario by default"
            echo ""
            echo "Options:"
            echo "  --frontend-only    Start only the frontend/backend manually"
            echo "  --services-only    Start only PM2-managed services (no manual frontend)"
            echo "  --data-dump        Start recording immediately (also enables attack execution)"
            echo "  --scenario <file>  Load a scenario explicitly"
            echo "  --help, -h         Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                                    # Start simulator shell, no scenario loaded"
            echo "  $0 --frontend-only                   # Start only the web interface"
            echo "  $0 --scenario scenarios/Town01_sumo_2.yaml"
            echo "  $0 --services-only --data-dump --scenario scenarios/Town01_sumo_2.yaml"
            exit 0
            ;;
        *)
            echo -e "${RED}Error: Unknown option or positional scenario: $1${NC}"
            echo "Use --scenario <file> to load a scenario, or run $0 with no arguments to start without one."
            exit 1
            ;;
    esac
done

DISPLAY_HOST="${PUBLIC_HOST:-$(get_primary_ip)}"
if [ -z "$DISPLAY_HOST" ]; then
    DISPLAY_HOST="localhost"
fi

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}    Simulator Startup Script${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if global config exists
if [ ! -f "config/global.config.yaml" ]; then
    echo -e "${RED}Error: config/global.config.yaml not found${NC}"
    echo "Please create the global configuration file with your installation paths."
    echo "See config/README.md for details."
    exit 1
fi

# Start Frontend
if [ "$START_FRONTEND" = true ]; then
    echo -e "${GREEN}[1/3] Preparing Frontend Backend...${NC}"

    # Ensure backend dependencies exist for either manual start or PM2
    if [ ! -d "frontend/server/node_modules" ]; then
        echo -e "${YELLOW}Installing backend dependencies...${NC}"
        cd frontend/server
        npm install
        cd ../..
    fi

    # Ensure frontend dependencies exist for either manual start or PM2
    if [ ! -d "frontend/client/node_modules" ]; then
        echo -e "${YELLOW}Installing frontend dependencies...${NC}"
        cd frontend/client
        npm install
        cd ../..
    fi

    if [ "$START_SERVICES" = true ]; then
        echo -e "${GREEN}✓ Frontend processes will be managed by PM2 (frontend-backend & frontend-client)${NC}"
        echo "  Logs: pm2 logs frontend-backend | pm2 logs frontend-client"
        echo ""
        rm -f .backend.pid .frontend.pid
    else
        # Start backend server in background when no PM2 services are running
        cd frontend/server
        npm start > ../../logs/backend.log 2>&1 &
        BACKEND_PID=$!
        cd ../..

        echo -e "${GREEN}✓ Backend server started (PID: $BACKEND_PID)${NC}"
        echo "  Logs: logs/backend.log"
        echo "  API local: http://localhost:3001"
        echo "  API LAN:   http://${DISPLAY_HOST}:3001"
        echo ""

        sleep 2

        echo -e "${GREEN}[2/3] Starting Frontend Client...${NC}"

        cd frontend/client
        npm run dev -- --host 0.0.0.0 --port 5173 > ../../logs/frontend.log 2>&1 &
        FRONTEND_PID=$!
        cd ../..

        echo -e "${GREEN}✓ Frontend client started (PID: $FRONTEND_PID)${NC}"
        echo "  Logs: logs/frontend.log"
        echo "  URL local: http://localhost:5173"
        echo "  URL LAN:   http://${DISPLAY_HOST}:5173"
        echo ""

        # Save PIDs for cleanup
        echo "$BACKEND_PID" > .backend.pid
        echo "$FRONTEND_PID" > .frontend.pid
    fi
fi

# Start Simulator Services
if [ "$START_SERVICES" = true ]; then
    echo -e "${GREEN}[3/3] Starting Simulator Services...${NC}"

    if [ -n "$SCENARIO" ]; then
        # Check if scenario file exists
        if [ ! -f "$SCENARIO" ]; then
            echo -e "${RED}Error: Scenario file not found: $SCENARIO${NC}"
            echo "Available scenarios:"
            ls -1 scenarios/*.yaml 2>/dev/null || echo "  No scenarios found"
            exit 1
        fi

        echo -e "${YELLOW}Scenario: $SCENARIO${NC}"
        if [ "$DATA_DUMP" = true ]; then
            echo -e "${YELLOW}Recording: enabled from startup${NC}"
        fi

        # Load scenario to show details
        if command -v python3 &> /dev/null; then
            SCENARIO_INFO=$(python3 -c "
import yaml
import sys
try:
    with open('$SCENARIO', 'r') as f:
        config = yaml.safe_load(f)
    mode = config.get('mode', 'sumo_cosim')
    town = config.get('carla', {}).get('town', 'Unknown')
    print(f'Mode: {mode}')
    print(f'Town: {town}')
except Exception as e:
    print(f'Error reading scenario: {e}')
" 2>/dev/null)
            if [ -n "$SCENARIO_INFO" ]; then
                echo "$SCENARIO_INFO" | while read line; do
                    echo -e "${YELLOW}  $line${NC}"
                done
            fi
        fi
    else
        echo -e "${YELLOW}No scenario specified. Starting simulator shell only; no CARLA/SUMO scenario will be loaded.${NC}"
    fi

    echo ""
    echo -e "${YELLOW}Starting PM2 services...${NC}"

    # Stop any existing services
    pm2 delete all 2>/dev/null || true
    sleep 1

    if [ -n "$SCENARIO" ]; then
        SIM_CONFIG="$SCENARIO" DATA_DUMP="$DATA_DUMP" pm2 start config/simulator.config.js
    else
        DATA_DUMP="$DATA_DUMP" pm2 start config/simulator.config.js --only frontend-backend
        wait_for_port "127.0.0.1" "3001" "Frontend backend" 30 || true
        DATA_DUMP="$DATA_DUMP" pm2 start config/simulator.config.js --only frontend-client
    fi

    echo ""
    echo -e "${GREEN}✓ Simulator services started${NC}"
    echo ""
    pm2 status
fi

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}✓ Startup complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

if [ "$START_FRONTEND" = true ]; then
    echo -e "${GREEN}Frontend:${NC}"
    echo "  • Web Interface local: ${BLUE}http://localhost:5173${NC}"
    echo "  • Web Interface LAN:   ${BLUE}http://${DISPLAY_HOST}:5173${NC}"
    echo "  • Backend API local:   ${BLUE}http://localhost:3001${NC}"
    echo "  • Backend API LAN:     ${BLUE}http://${DISPLAY_HOST}:3001${NC}"
    echo ""
fi

if [ "$START_SERVICES" = true ]; then
    echo -e "${GREEN}Simulator Services:${NC}"
    echo "  • PM2 Dashboard: ${BLUE}pm2 status${NC}"
    echo "  • View Logs:     ${BLUE}pm2 logs${NC}"
    if [ -n "$SCENARIO" ]; then
        echo "  • Image Service: ${BLUE}http://localhost:5000/health${NC}"
    else
        echo "  • Scenario:      ${YELLOW}not loaded${NC}"
    fi
    echo ""
fi

echo -e "${YELLOW}Control Commands:${NC}"
echo "  • Stop frontend:   ./stop.sh --frontend"
echo "  • Stop services:   ./stop.sh --services"
echo "  • Stop all:        ./stop.sh"
echo "  • View status:     pm2 status"
echo ""

# Keep script running if both frontend and services are started
if [ "$START_FRONTEND" = true ] && [ "$START_SERVICES" = true ]; then
    if [ -n "$BACKEND_PID" ] || [ -n "$FRONTEND_PID" ]; then
        echo -e "${BLUE}Press Ctrl+C to view shutdown options${NC}"
        echo ""

        # Trap SIGINT (Ctrl+C)
        trap 'echo ""; echo "Use ./stop.sh to stop services"; exit 0' INT

        # Monitor processes
        while true; do
            sleep 5
            # Check if backend is still running
            if [ ! -z "$BACKEND_PID" ] && ! kill -0 $BACKEND_PID 2>/dev/null; then
                echo -e "${RED}Backend server stopped unexpectedly${NC}"
                break
            fi
            # Check if frontend is still running
            if [ ! -z "$FRONTEND_PID" ] && ! kill -0 $FRONTEND_PID 2>/dev/null; then
                echo -e "${RED}Frontend client stopped unexpectedly${NC}"
                break
            fi
        done
    fi
fi
