# Installation Guide

This document records the simulator stack expected by this research-paper repository:

- CARLA 0.9.15 / 0.9.15-dirty
- SUMO with TraCI and `netconvert`
- OMNeT++ 5.7.1 with Artery

The repo reads machine-specific simulator paths from `config/global.config.yaml`. Keep source code and scenario YAML in the repo, and put large simulator installations outside the repo.

## 1. Base System Dependencies

The project is developed for Ubuntu/Linux. Install the common tooling first:

```bash
sudo apt update
sudo apt install -y \
  build-essential cmake git curl wget unzip tar \
  python3 python3-pip python3-venv \
  nodejs npm \
  netcat-openbsd lsof

sudo npm install -g pm2
pip3 install --user -r python_scripts/requirements.txt
```

If your Node.js version is older than 18, install a newer Node.js release before running the frontend.

## 2. CARLA Installation

This repository is configured for CARLA 0.9.15. Use an installation outside this repository, for example:

```text
$HOME/simulators/CARLA_0.9.15/LinuxNoEditor
```

That directory must contain:

```text
CarlaUE4.sh
PythonAPI/carla/dist/carla-0.9.15-py3.8-linux-x86_64.egg
```

### Fresh Install

Download the CARLA 0.9.15 Linux package and extract it outside this repository, for example:

```bash
mkdir -p "$HOME/simulators"
cd "$HOME/simulators"

# Put the CARLA 0.9.15 Linux tarball here, then extract it.
tar -xzf CARLA_0.9.15.tar.gz

# Use the extracted LinuxNoEditor directory as the CARLA installation path.
chmod +x CARLA_0.9.15/LinuxNoEditor/CarlaUE4.sh
```

If your extracted directory name differs, that is fine. Use the real path in `config/global.config.yaml`.

### Configure CARLA Environment

Set `CARLA_HOME` to the CARLA `LinuxNoEditor` directory:

```bash
export CARLA_HOME="$HOME/simulators/CARLA_0.9.15/LinuxNoEditor"
export PYTHONPATH="$CARLA_HOME/PythonAPI/carla/dist/carla-0.9.15-py3.8-linux-x86_64.egg:$CARLA_HOME/PythonAPI/carla:$PYTHONPATH"
```

Add those exports to `~/.bashrc` if this is your normal installation path.

### Validate CARLA

Start CARLA in headless mode:

```bash
cd "$CARLA_HOME"
./CarlaUE4.sh -RenderOffScreen -carla-rpc-port=2000 -quality-level=Low -nosound
```

In another terminal:

```bash
python3 - <<'PY'
import carla
client = carla.Client("127.0.0.1", 2000)
client.set_timeout(10.0)
print(client.get_world().get_map().name)
PY
```

The repo launcher uses `scripts/start_carla_headless.sh`, so the same path must also be present in `config/global.config.yaml`.

## 3. SUMO Installation

The repo expects SUMO to provide:

- `sumo`
- `sumo-gui`
- `netconvert`
- Python packages `traci` and `sumolib`

The current local setup uses:

```text
SUMO_HOME=/usr/share/sumo
sumo executable=/usr/bin/sumo
```

Install SUMO:

```bash
sudo apt install -y sumo sumo-tools sumo-doc
```

Set the environment:

```bash
export SUMO_HOME=/usr/share/sumo
export PYTHONPATH="$SUMO_HOME/tools:$PYTHONPATH"
```

Add those exports to `~/.bashrc`.

Validate SUMO:

```bash
sumo --version
netconvert --version

python3 - <<'PY'
import traci
import sumolib
print("traci/sumolib import OK")
PY
```

### Generate CARLA Town Networks for SUMO

The `.sumocfg` files under `sumo_configs/` require matching `.net.xml` and `.rou.xml` files. If `Town01.net.xml`, `Town03.net.xml`, or `Town04.net.xml` are missing, generate them from CARLA OpenDRIVE.

Start CARLA first, then run:

```bash
cd /path/to/simulator

export CARLA_HOME="$HOME/simulators/CARLA_0.9.15/LinuxNoEditor"
export PYTHONPATH="$CARLA_HOME/PythonAPI/carla/dist/carla-0.9.15-py3.8-linux-x86_64.egg:$CARLA_HOME/PythonAPI/carla:$PYTHONPATH"

python3 scripts/export_opendrive.py Town01 --output-dir sumo_configs

netconvert --opendrive sumo_configs/Town01.xodr \
  --output-file sumo_configs/Town01.net.xml \
  --geometry.min-radius.fix.railways false \
  --offset.disable-normalization true \
  --no-internal-links false \
  --junctions.corner-detail 0

python3 "$SUMO_HOME/tools/randomTrips.py" \
  -n sumo_configs/Town01.net.xml \
  -r sumo_configs/Town01.rou.xml \
  -e 3600 \
  -p 2.0 \
  --fringe-factor 5 \
  --min-distance 300 \
  --trip-attributes='departLane="best" departSpeed="max"'
```

Repeat the same pattern for `Town03`, `Town04`, or any other CARLA town used by a scenario.

Validate a SUMO config:

```bash
sumo -c sumo_configs/Town01.sumocfg --start --quit-on-end
```

For CARLA/SUMO co-simulation with Artery enabled, keep `sumo.num_clients: 2` in the scenario YAML. Artery connects as client order 1, and the sync bridge connects as client order 2.

## 4. Artery and OMNeT++ Installation

Artery requires OMNeT++. A typical layout is:

```text
OMNeT++: $HOME/omnetpp-5.7.1
Artery: $HOME/src/artery
Artery build: $HOME/src/artery/build
Upstream: https://github.com/riebl/artery.git
Local base commit: 51a00eb5
```

This local Artery tree also contains project-specific ExternalControl changes. For exact reproduction, use the paper's patched Artery tree or apply the same local patch on top of upstream Artery.

### Install OMNeT++ 5.7.1

Download the OMNeT++ 5.7.1 source archive, then build it:

```bash
cd ~
tar -xzf omnetpp-5.7.1-src-linux.tgz
cd omnetpp-5.7.1

source setenv
./configure
make -j"$(nproc)"
```

Add OMNeT++ to your shell startup:

```bash
echo 'export OMNETPP_ROOT="$HOME/omnetpp-5.7.1"' >> ~/.bashrc
echo 'source "$OMNETPP_ROOT/setenv"' >> ~/.bashrc
```

Validate OMNeT++:

```bash
source ~/omnetpp-5.7.1/setenv
opp_run -h | head
```

### Build Artery

Clone Artery outside this repository:

```bash
mkdir -p "$HOME/src"
cd "$HOME/src"
git clone --recurse-submodules https://github.com/riebl/artery.git
cd artery

# Optional: checkout the local base commit used by this paper setup.
git checkout 51a00eb5
git submodule update --init --recursive
```

Build with OMNeT++ on the path:

```bash
source ~/omnetpp-5.7.1/setenv
cd "$HOME/src/artery"

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DRUN_FLAGS="-u Cmdenv"
cmake --build build -j"$(nproc)"
```

Validate the Artery build:

```bash
cd "$HOME/src/artery"
cmake --build build --target run_integrated-simulator
```

For normal repo execution, Artery should be launched by PM2 after SUMO is online. The launcher scripts are:

```text
scripts/start_artery_service.sh
scripts/run_artery.sh
```

The Artery scenario INI used by this repo is usually either:

```text
$ARTERY_HOME/scenarios/integrated-simulator/omnetpp.ini
python_scripts/carla/Co-Simulation/arterysim/sumo-omnetpp-med.ini
```

Make sure the selected INI uses TraCI client order 1 when sharing SUMO with the CARLA/SUMO sync bridge.

## 5. Configure This Repository

Edit `config/global.config.yaml` so it points to your actual installation paths:

```yaml
carla:
  installation_path: "/path/to/CARLA_0.9.15/LinuxNoEditor"
  executable: "/path/to/CARLA_0.9.15/LinuxNoEditor/CarlaUE4.sh"
  python_api_path: "/path/to/CARLA_0.9.15/LinuxNoEditor"
  scenario_runner_path: "/path/to/simulator/scenario_runner"
  scenario_runner_executable: "/path/to/simulator/scenario_runner/scenario_runner.py"

sumo:
  installation_path: "/usr/share/sumo"
  executable: "/usr/bin/sumo"
  tools_path: "/usr/share/sumo/tools"

artery:
  omnetpp_root: "/path/to/omnetpp-5.7.1"
  installation_path: "/path/to/artery"
  build_path: "/path/to/artery/build"
  executable: "/path/to/simulator/scripts/run_artery.sh"
```

Install Node dependencies:

```bash
npm install
npm install --prefix frontend/server
npm install --prefix frontend/client
```

## 6. End-to-End Validation

Start the frontend only:

```bash
./start.sh --frontend-only
```

Start services with a scenario:

```bash
./start.sh --services-only --scenario scenarios/Town01_sumo_2.yaml
```

Check service status and logs:

```bash
pm2 status
pm2 logs carla-headless
pm2 logs sumo-server
pm2 logs artery
pm2 logs sync-simulators
```

Stop everything:

```bash
./stop.sh
```
