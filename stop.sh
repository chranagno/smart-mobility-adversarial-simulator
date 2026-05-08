#!/bin/bash

# Simulator Stop Script
# This script stops the frontend and/or simulator services

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

STOP_FRONTEND=true
STOP_SERVICES=true

# Parse options
while [[ $# -gt 0 ]]; do
    case $1 in
        --frontend)
            STOP_SERVICES=false
            shift
            ;;
        --services)
            STOP_FRONTEND=false
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Stop the simulator frontend and/or services"
            echo ""
            echo "Options:"
            echo "  --frontend    Stop only the frontend (backend + client)"
            echo "  --services    Stop only simulator services"
            echo "  --help, -h    Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0              # Stop everything"
            echo "  $0 --frontend   # Stop only the web interface"
            echo "  $0 --services   # Stop only PM2 services"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}    Stopping Simulator${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Stop Frontend
if [ "$STOP_FRONTEND" = true ]; then
    echo -e "${YELLOW}Stopping Frontend...${NC}"

    # Stop backend
    if [ -f ".backend.pid" ]; then
        BACKEND_PID=$(cat .backend.pid)
        if kill -0 $BACKEND_PID 2>/dev/null; then
            kill $BACKEND_PID 2>/dev/null
            echo -e "${GREEN}✓ Backend server stopped (PID: $BACKEND_PID)${NC}"
        else
            echo -e "${YELLOW}Backend server not running${NC}"
        fi
        rm .backend.pid
    else
        # Try to find and kill the backend process
        BACKEND_PIDS=$(pgrep -f "frontend/server/index.js" || true)
        if [ -n "$BACKEND_PIDS" ]; then
            echo "$BACKEND_PIDS" | xargs kill 2>/dev/null || true
            echo -e "${GREEN}✓ Backend server stopped${NC}"
        else
            echo -e "${YELLOW}Backend server not running${NC}"
        fi
    fi

    # Stop frontend client
    if [ -f ".frontend.pid" ]; then
        FRONTEND_PID=$(cat .frontend.pid)
        if kill -0 $FRONTEND_PID 2>/dev/null; then
            kill $FRONTEND_PID 2>/dev/null
            echo -e "${GREEN}✓ Frontend client stopped (PID: $FRONTEND_PID)${NC}"
        else
            echo -e "${YELLOW}Frontend client not running${NC}"
        fi
        rm .frontend.pid
    else
        # Try to find and kill vite dev server
        VITE_PIDS=$(pgrep -f "vite.*frontend/client" || true)
        if [ -n "$VITE_PIDS" ]; then
            echo "$VITE_PIDS" | xargs kill 2>/dev/null || true
            echo -e "${GREEN}✓ Frontend client stopped${NC}"
        else
            echo -e "${YELLOW}Frontend client not running${NC}"
        fi
    fi

    # Also kill any node processes related to the frontend
    pkill -f "node.*frontend" 2>/dev/null || true

    echo ""
fi

# Stop Simulator Services
if [ "$STOP_SERVICES" = true ]; then
    echo -e "${YELLOW}Stopping Simulator Services...${NC}"

    # Stop PM2 services
    pm2 delete all 2>/dev/null && echo -e "${GREEN}✓ All PM2 services stopped${NC}" || echo -e "${YELLOW}No PM2 services running${NC}"

    # Stop any stray config-launcher instances started outside PM2
    CONFIG_LAUNCHER_PIDS=$(pgrep -f "python3 .*config_launcher_service.py|python .*config_launcher_service.py" || true)
    if [ -n "$CONFIG_LAUNCHER_PIDS" ]; then
        echo "$CONFIG_LAUNCHER_PIDS" | xargs kill 2>/dev/null || true
        echo -e "${GREEN}✓ Config launcher processes stopped${NC}"
    fi

    # Also kill any remaining Carla processes
    pkill -f "CarlaUE4" 2>/dev/null && echo -e "${GREEN}✓ Carla processes stopped${NC}" || true

    echo ""
fi

echo -e "${GREEN}✓ Shutdown complete${NC}"
echo ""
