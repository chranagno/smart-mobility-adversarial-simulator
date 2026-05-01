# Simulator Control Platform

> **Paper Reference:** This repository contains the code for the paper **"A Unified Interactive Simulation Platform for Smart Mobility: Adversarial Attack Generation in Connected Autonomous Vehicles"**.
> 
> **Acknowledgements:** This project acknowledges the conceptual and technical contributions of the following open-source platforms:
> - [OpenCDA](https://github.com/ucla-mobility/OpenCDA)
> - [CarlaSumoArtery-CoSimulation](https://github.com/mohammedLamine/CarlaSumoArtery-CoSimulation)

A comprehensive control platform for managing Carla, SUMO, and Artery simulators with a modern web interface and synchronized co-simulation.
---

## Features

- 🚀 **One-Command Startup** - Start frontend and simulators together
- 🌐 **Web Control Panel** - Modern React-based UI for full control
- 🎭 **Dual Simulation Modes** - SUMO co-simulation or Carla Scenario Runner
- 🔄 **Synchronized Co-Simulation** - CARLA ↔ SUMO ↔ Artery synchronization
- 📡 **V2X Communication** - Artery-based Vehicle-to-Everything simulation
- 📊 **Real-time Monitoring** - Live service status, logs, and metrics
- 📹 **BEV Camera System** - Bird's-eye view image capture and streaming
- ⚙️ **Global Configuration** - Centralized path management
- 🎯 **Scenario Selection** - Choose and launch scenarios from the UI
- ⏱️ **Artery-Controlled Stepping** - V2X framework drives simulation timing

---

## Prerequisites

- Ubuntu/WSL (recommended)
- Node.js 18+ and npm
- Python 3.8+
- PM2 process manager
- Carla 0.9.15
- SUMO 1.12+
- OMNeT++ 5.6+ (optional, for Artery)


## Quick Start

### 1. Install Dependencies

```bash
# Install Node.js and npm (if not already installed)
sudo apt update && sudo apt install nodejs npm -y

# Install PM2 globally
sudo npm install -g pm2

# Install Python dependencies
pip3 install Flask flask-cors Pillow numpy PyYAML
```

### 2. Configure Installation Paths

Edit `config/global.config.yaml` with your installation paths:

```yaml
carla:
  installation_path: "/path/to/carla/LinuxNoEditor"
  python_api_path: "/path/to/carla/LinuxNoEditor"
  scenario_runner_path: "/path/to/scenario_runner"
  scenario_runner_executable: "/path/to/scenario_runner/scenario_runner.py"

sumo:
  installation_path: "/usr/share/sumo"
  executable: "/usr/bin/sumo"
  tools_path: "/usr/share/sumo/tools"

artery:
  omnetpp_root: "/path/to/omnetpp-5.7.1"
  installation_path: "/path/to/artery"
  build_path: "/path/to/artery/build"
```

See [config/README.md](config/README.md) for details.

### 3. Start Everything

```bash
# Start with default scenario
./start.sh

# Or specify a scenario
./start.sh scenarios/Town04.config.yaml

# Frontend only (no simulator services)
./start.sh --frontend-only

# Services only (no web interface)
./start.sh --services-only
```

The web interface will be available at: **http://localhost:5173**

---

## Usage

### Starting the System

```bash
./start.sh [OPTIONS] [SCENARIO]

Options:
  --frontend-only    Start only the web interface
  --services-only    Start only simulator services
  --scenario <file>  Specify scenario configuration

Examples:
  ./start.sh                                    # Default scenario
  ./start.sh scenarios/Town04.config.yaml      # Specific scenario
  ./start.sh --frontend-only                   # Web interface only
```

### Checking Status

```bash
./status.sh    # Show status of all components
pm2 status     # Show PM2 services only
pm2 logs       # View service logs
```

### Stopping the System

```bash
./stop.sh            # Stop everything
./stop.sh --frontend # Stop web interface only
./stop.sh --services # Stop simulator services only
```

---

## Simulation Modes

The platform supports two simulation modes with **synchronized co-simulation** between CARLA, SUMO, and optionally Artery.

### Mode 1: SUMO Co-Simulation

Run Carla with SUMO for traffic simulation, with optional Artery V2X communication.

**Configuration:**
```yaml
mode: "sumo_cosim"

carla:
  town: "Town01"
  port: 2000
  quality: "Low"

sumo:
  enabled: true
  config: "sumo_configs/Town01.sumocfg"
  host: "localhost"
  port: 8813
  num_clients: 2  # Artery + sync script
  gui: false      # Set to true for SUMO GUI

artery:
  enabled: true   # Enable V2X communication
  ini_path: "/path/to/artery/omnetpp.ini"

sync:
  enabled: true
  step_length: 0.05      # 20 Hz simulation
  tls_manager: "carla"   # Traffic light control
```

**Services Started:**
1. `carla-headless` - CARLA simulator server
2. `config-launcher` - Loads map into CARLA
3. `sumo-server` - SUMO traffic simulation with TraCI
4. `sync-simulators` - Synchronization bridge (connects to SUMO, starts Artery)
5. `image-capture` - BEV camera service

**How It Works:**
- SUMO runs as a separate TraCI server
- Artery (if enabled) connects to SUMO with **client order 1** (controls simulation step)
- Sync script connects to SUMO with **client order 2** (follows Artery)
- Vehicles, traffic lights, and state synchronized between all simulators
- **Artery drives the simulation timing** via TraCI

---

### Mode 2: Carla Scenario Runner

Run OpenSCENARIO files with synchronized SUMO background traffic and Artery V2X.

**Configuration:**
```yaml
mode: "carla_scenario"

carla:
  town: "Town01"
  port: 2000

scenario_runner:
  scenario_file: "scenario_runner/examples/LaneChangeSimple.xosc"
  timeout: 300
  output_dir: "./scenario_results"

sumo:
  enabled: true
  config: "sumo_configs/Town01.sumocfg"
  port: 8813
  num_clients: 2

artery:
  enabled: true
  ini_path: "/path/to/artery/omnetpp.ini"

sync:
  enabled: true
  step_length: 0.05
  tls_manager: "carla"
```

**Services Started:**
1. `carla-headless` - CARLA simulator server
2. `scenario-runner` - Loads OpenSCENARIO scenario
3. `sumo-server` - SUMO background traffic with TraCI
4. `sync-simulators` - Synchronization bridge (connects to SUMO, starts Artery)
5. `image-capture` - BEV camera service

**How It Works:**
- Scenario Runner loads and executes OpenSCENARIO file
- SUMO provides synchronized background traffic
- Artery enables V2X communication between scenario and background vehicles
- **Artery controls simulation stepping** for realistic V2X timing

---

## Synchronization Architecture

The platform uses a **sync-centric architecture** where `sync_simulators.py` acts as the bridge between CARLA, SUMO, and Artery.

### Architecture Overview

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   CARLA     │◄────────┤    Sync     │────────►│    SUMO     │
│  Simulator  │  Sync   │   Script    │  TraCI  │   Server    │
└─────────────┘  Loop   └─────────────┘  order=2└─────────────┘
                              │                        ▲
                              │ Starts                 │ TraCI
                              ▼                        │ order=1
                        ┌─────────────┐                │
                        │   Artery    │────────────────┘
                        │  OMNeT++    │    Controls Step
                        └─────────────┘
```

### How Synchronization Works

**1. Startup Sequence:**
- CARLA starts and loads map/scenario
- SUMO starts as independent TraCI server
- Sync script connects to CARLA and SUMO
- Sync script optionally starts Artery subprocess
- Artery connects to SUMO via TraCI

**2. TraCI Client Order:**
- **Artery**: Client order = 1 (configured in `sumo-omnetpp-med.ini`)
- **Sync Script**: Client order = 2 (configured in PM2)
- Lower order executes first → **Artery controls simulation step**

**3. Simulation Loop:**
```
Each simulation step (default: 0.05s):
1. SUMO waits for all TraCI clients
2. Artery executes first (order=1)
   - Processes V2X messages (CAM, DENM, etc.)
   - Updates vehicle intentions
3. Sync script executes (order=2)
   - Syncs CARLA vehicles → SUMO
   - Syncs SUMO vehicles → CARLA
   - Syncs traffic lights
4. SUMO advances simulation
5. Repeat
```

**4. What Gets Synchronized:**
- Vehicle positions and velocities
- Traffic light states
- Vehicle spawning/despawning
- Optional: Vehicle lights and colors

### Configuration Files

- **PM2 Config**: `config/simulator.config.js` - Service orchestration
- **Artery TraCI**: `python_scripts/carla/Co-Simulation/arterysim/sumo-omnetpp-med.ini` - Client order=1
- **Sync Settings**: `scenarios/*.yaml` - Step length, TLS manager, sync options

---

## Simulation Lifecycle Management

The platform uses a **state machine** to manage the simulation lifecycle, ensuring valid state transitions and proper error handling.

### State Machine

The `SimulationManager` implements a centralized state machine with the following states:

- **STOPPED** - No services running, ready to load a scenario
- **LOADING** - Infrastructure services starting (carla, config-launcher, sumo, artery)
- **LOADED** - Infrastructure ready, simulation can be started
- **STARTING** - sync-simulators launching
- **RUNNING** - Simulation active and running
- **STOPPING** - Services shutting down
- **ERROR** - Error state (can recover to STOPPED or LOADING)

### State Flow

```
STOPPED → LOADING → LOADED → STARTING → RUNNING
   ↑                                    ↓
   └──────────────── STOPPING ←─────────┘
         ↑                    ↓
         └────── ERROR ───────┘
```

## Web Control Panel

The web interface provides:

- **Scenario Management** - Select and launch scenarios from the UI
- **Service Control** - Start/stop/restart individual services
- **Real-time Monitoring** - CPU, memory, status updates
- **Log Viewer** - View service logs in real-time
- **Camera Viewer** - Bird's-eye view image capture and video streaming
- **State Machine** - Centralized simulation lifecycle management with validated state transitions

---

Happy Simulating! 🚀

