#!/bin/bash

# Simulator Status Script
# Shows the status of frontend and simulator services

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}    Simulator Status${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check Frontend
echo -e "${BLUE}Frontend Services:${NC}"
echo ""

# Backend Server
BACKEND_RUNNING=false
if [ -f ".backend.pid" ]; then
    BACKEND_PID=$(cat .backend.pid)
    if kill -0 $BACKEND_PID 2>/dev/null; then
        echo -e "  Backend Server:  ${GREEN}RUNNING${NC} (PID: $BACKEND_PID)"
        echo -e "                   ${BLUE}http://localhost:3001${NC}"
        BACKEND_RUNNING=true
    else
        echo -e "  Backend Server:  ${RED}STOPPED${NC} (stale PID file)"
    fi
else
    BACKEND_PID=$(pgrep -f "frontend/server/index.js" | head -1)
    if [ -n "$BACKEND_PID" ]; then
        echo -e "  Backend Server:  ${GREEN}RUNNING${NC} (PID: $BACKEND_PID)"
        echo -e "                   ${BLUE}http://localhost:3001${NC}"
        BACKEND_RUNNING=true
    else
        echo -e "  Backend Server:  ${RED}STOPPED${NC}"
    fi
fi

# Frontend Client
FRONTEND_RUNNING=false
if [ -f ".frontend.pid" ]; then
    FRONTEND_PID=$(cat .frontend.pid)
    if kill -0 $FRONTEND_PID 2>/dev/null; then
        echo -e "  Frontend Client: ${GREEN}RUNNING${NC} (PID: $FRONTEND_PID)"
        echo -e "                   ${BLUE}http://localhost:5173${NC}"
        FRONTEND_RUNNING=true
    else
        echo -e "  Frontend Client: ${RED}STOPPED${NC} (stale PID file)"
    fi
else
    FRONTEND_PID=$(pgrep -f "vite.*frontend/client" | head -1)
    if [ -n "$FRONTEND_PID" ]; then
        echo -e "  Frontend Client: ${GREEN}RUNNING${NC} (PID: $FRONTEND_PID)"
        echo -e "                   ${BLUE}http://localhost:5173${NC}"
        FRONTEND_RUNNING=true
    else
        echo -e "  Frontend Client: ${RED}STOPPED${NC}"
    fi
fi

echo ""

# Check Simulator Services
echo -e "${BLUE}Simulator Services (PM2):${NC}"
echo ""

# Check if PM2 has any processes
PM2_COUNT=$(pm2 list | grep -c "online\|stopped\|errored" 2>/dev/null || echo "0")

if [ "$PM2_COUNT" -gt 0 ]; then
    pm2 status
    echo ""

    # Check for current scenario
    if [ -f ".scenario_status.json" ]; then
        echo -e "${BLUE}Current Scenario:${NC}"
        if command -v python3 &> /dev/null; then
            python3 -c "
import json
try:
    with open('.scenario_status.json', 'r') as f:
        status = json.load(f)
    print(f\"  Scenario: {status.get('scenario', 'Unknown')}\")
    print(f\"  Mode:     {status.get('mode', 'Unknown')}\")
    print(f\"  Town:     {status.get('town', 'Unknown')}\")
    print(f\"  Started:  {status.get('startedAt', 'Unknown')}\")
except Exception as e:
    print(f\"  Error reading status: {e}\")
" 2>/dev/null
        else
            cat .scenario_status.json
        fi
        echo ""
    fi
else
    echo -e "  ${YELLOW}No PM2 services running${NC}"
    echo ""
fi

# Check Image Capture Service
echo -e "${BLUE}Image Capture Service:${NC}"
echo ""

if command -v curl &> /dev/null; then
    HEALTH_RESPONSE=$(curl -s http://localhost:5000/health 2>/dev/null || echo "")
    if [ -n "$HEALTH_RESPONSE" ]; then
        echo -e "  Service:    ${GREEN}RUNNING${NC}"
        echo -e "  Endpoint:   ${BLUE}http://localhost:5000${NC}"

        # Parse health response if python is available
        if command -v python3 &> /dev/null; then
            echo "$HEALTH_RESPONSE" | python3 -c "
import json
import sys
try:
    data = json.load(sys.stdin)
    connected = data.get('connected', False)
    camera_active = data.get('camera_active', False)
    conn_status = 'CONNECTED' if connected else 'DISCONNECTED'
    cam_status = 'ACTIVE' if camera_active else 'INACTIVE'
    print(f'  Carla:      {conn_status}')
    print(f'  Camera:     {cam_status}')
except:
    pass
" 2>/dev/null
        fi
    else
        echo -e "  Service:    ${RED}STOPPED${NC}"
    fi
else
    echo -e "  ${YELLOW}curl not available - cannot check service status${NC}"
fi

echo ""

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Summary:${NC}"
echo ""

EVERYTHING_RUNNING=true

if [ "$BACKEND_RUNNING" = true ] && [ "$FRONTEND_RUNNING" = true ]; then
    echo -e "  Frontend:   ${GREEN}✓ All systems operational${NC}"
else
    echo -e "  Frontend:   ${YELLOW}⚠ Some services stopped${NC}"
    EVERYTHING_RUNNING=false
fi

if [ "$PM2_COUNT" -gt 0 ]; then
    ONLINE_COUNT=$(pm2 list | grep -c "online" 2>/dev/null || echo "0")
    if [ "$ONLINE_COUNT" -gt 0 ]; then
        echo -e "  Simulator:  ${GREEN}✓ $ONLINE_COUNT service(s) running${NC}"
    else
        echo -e "  Simulator:  ${YELLOW}⚠ No services online${NC}"
        EVERYTHING_RUNNING=false
    fi
else
    echo -e "  Simulator:  ${RED}✗ Not started${NC}"
    EVERYTHING_RUNNING=false
fi

echo ""

if [ "$EVERYTHING_RUNNING" = true ]; then
    echo -e "${GREEN}✓ All systems operational${NC}"
else
    echo -e "${YELLOW}Use ./start.sh to start all services${NC}"
fi

echo ""
