# SUMO Configuration Files for Carla Towns

This directory contains SUMO configuration files that can be used alongside Carla simulations.

## Generating SUMO Networks from Carla Maps

To create proper SUMO network files from Carla towns, you need to:

### 1. Export OpenDRIVE from Carla

```python
import carla

client = carla.Client('localhost', 2000)
world = client.get_world()

# Get the OpenDRIVE map
opendrive_map = world.get_map().to_opendrive()

# Save to file
with open('Town01.xodr', 'w') as f:
    f.write(opendrive_map)
```

### 2. Convert OpenDRIVE to SUMO Network

```bash
# Using SUMO's netconvert tool
netconvert --opendrive Town01.xodr \
    -o Town01.net.xml \
    --geometry.min-radius.fix.railways false \
    --offset.disable-normalization true \
    --no-internal-links false \
    --junctions.corner-detail 0
```

### 3. Generate Random Traffic

```bash
# Generate random trips
python $SUMO_HOME/tools/randomTrips.py \
    -n Town01.net.xml \
    -r Town01.rou.xml \
    -e 3600 \
    -p 2.0 \
    --fringe-factor 5 \
    --min-distance 300 \
    --trip-attributes="departLane=\"best\" departSpeed=\"max\""
```

### 4. (Optional) Generate Routes from Trips

If randomTrips generates trips instead of routes:

```bash
duarouter -n Town01.net.xml \
    -t Town01.trips.xml \
    -o Town01.rou.xml \
    --ignore-errors
```

## Available Configurations

### Town01.sumocfg
- Basic configuration for Carla Town01
- Requires: `Town01.net.xml`, `Town01.rou.xml`

### Town03.sumocfg
- Configuration for Carla Town03 (larger urban map)
- Requires: `Town03.net.xml`, `Town03.rou.xml`

### Town04.sumocfg
- Configuration for Carla Town04 (highway)
- Requires: `Town04.net.xml`, `Town04.rou.xml`

### Town05.sumocfg
- Configuration for Carla Town05 (urban with bridge)
- Requires: `Town05.net.xml`, `Town05.rou.xml`

## Using with Carla Scenario Runner

Add SUMO configuration to your scenario YAML file:

```yaml
mode: "carla_scenario"

carla:
  town: "Town01"
  port: 2000

scenario_runner:
  scenario_file: "path/to/scenario.xosc"

# Enable SUMO background traffic
sumo:
  enabled: true
  config: "sumo_configs/Town01.sumocfg"
  port: 8813
  num_clients: 2
```

## Configuration Parameters

### sumocfg file structure:

- **net-file**: SUMO network file (generated from OpenDRIVE)
- **route-files**: Vehicle routes/traffic definition
- **begin/end**: Simulation time range (seconds)
- **step-length**: Simulation step size (default: 0.05s for Carla sync)
- **remote-port**: TraCI port for Carla-SUMO bridge

## Quick Start (Using Existing Network)

If you already have Carla's Python API installed:

```bash
# 1. Export OpenDRIVE from Carla
cd sumo_configs
python3 ../scripts/export_opendrive.py Town01

# 2. Convert to SUMO
netconvert --opendrive Town01.xodr -o Town01.net.xml

# 3. Generate traffic
python3 $SUMO_HOME/tools/randomTrips.py -n Town01.net.xml -r Town01.rou.xml -e 3600

# 4. Test the configuration
sumo -c Town01.sumocfg --start --quit-on-end
```

## Notes

- The provided `.rou.xml` files are placeholders
- You must generate proper network and route files for your specific use case
- SUMO step-length (0.05s) matches Carla's default simulation step
- Remote port (8813) must match Carla-SUMO bridge configuration
- For more traffic, adjust `probability` in flow definitions or reduce `-p` in randomTrips

## Troubleshooting

### "Edge not found" errors
- Your route references edges that don't exist in the network
- Regenerate routes using the correct network file

### No vehicles spawning
- Check that routes are valid for the network
- Increase traffic probability/reduce period
- Verify SUMO can connect via TraCI (port 8813)

### Carla-SUMO synchronization issues
- Ensure step-length matches (both should use 0.05s)
- Check that num_clients matches number of SUMO connections expected

## References

- [SUMO netconvert Documentation](https://sumo.dlr.de/docs/netconvert.html)
- [SUMO randomTrips.py](https://sumo.dlr.de/docs/Tools/Trip.html#randomtripspy)
- [Carla-SUMO Co-Simulation](https://carla.readthedocs.io/en/latest/adv_sumo/)
