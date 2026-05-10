# A Unified Interactive Simulation Platform for Smart Mobility: Adversarial Attack Generation in Connected Autonomous Vehicles


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
  python_api_path: "/path/to/carla/PythonAPI"


sumo:
  installation_path: "/usr/share/sumo"
  executable: "/usr/bin/sumo"
  tools_path: "/usr/share/sumo/tools"

artery:
  omnetpp_root: "/path/to/omnetpp-5.7.1"
  installation_path: "/path/to/artery"
  build_path: "/path/to/artery/build"
```


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
**How It Works:**
- SUMO runs as a separate TraCI server
- Artery  connects to SUMO with  (controls simulation step)
- Sync script connects to SUMO with  (follows Artery)
- Vehicles, traffic lights, and state synchronized between all simulators
- **Artery drives the simulation timing** via TraCI

---

## Synchronization Architecture

The platform uses a **sync-centric architecture** where `orchestrator.py` acts as the bridge between CARLA, SUMO, Artery, and the Adversarial Attack Module.

### Architecture Overview

```text
                                        ┌───────────────┐
                                        │  Adversarial  │
                                        │ Attack Server │
                                        │  (REST API)   │
                                        └───────────────┘
                                                ▲
                                                │ HTTP Calls
                                                ▼
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
- Sync script connects to the Adversarial Attack Server via REST

**2. TraCI Client Order:**
- **Artery**: Client order = 1 (configured in `sumo-omnetpp-med.ini`)
- **Sync Script**: Client order = 2 (configured in PM2)
- Lower order executes first → **Artery controls simulation step**

**3. Simulation Loop:**
```text
Each simulation step (default: 0.05s):
1. SUMO waits for all TraCI clients
2. Artery executes first (order=1)
   - Processes V2X messages (CAM, DENM, etc.)
   - Updates vehicle intentions
3. Sync script executes (order=2)
   - Syncs CARLA vehicles → SUMO
   - Syncs SUMO vehicles → CARLA
   - Syncs traffic lights
   - Collects ego sensor data and runs adversarial attacks via REST API
4. SUMO advances simulation
5. Repeat
```

**4. What Gets Synchronized:**
- Vehicle positions and velocities
- Traffic light states
- Vehicle spawning/despawning
- Optional: Vehicle lights and colors

---

## Adversarial Attack Module

This platform includes a decoupled module for generating and evaluating adversarial perception attacks. It runs side-by-side with the co-simulation, keeping the simulation environment separate from model inference and gradient computation.

**Key Features:**
- **REST-based Architecture:** The simulator communicates with a separate FastAPI model server via HTTP, passing sensor arrays and retrieving gradients, scalar losses, or inference outputs.
- **Implemented Attacks:** Supports 3D LiDAR attacks (Perturbation, Detachment, Attachment) and 2D Segmentation attacks (PGD, Square).
- **Decoupled Environment:** Model-specific dependencies like PyTorch, OpenPCDet, and YOLOP are isolated in the `model_server` Docker container.

For detailed instructions on architecture, supported attacks, and how to run the model server, please refer to the [Adversarial Attack Module README](adversarial_attack_module/README.md).

### Configuration Files

- **PM2 Config**: `config/simulator.config.js` - Service orchestration
- **Artery TraCI**: `src/carla/Co-Simulation/arterysim/sumo-omnetpp-med.ini` - Client order=1
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

