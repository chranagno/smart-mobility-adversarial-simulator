# Teleoperation

This folder contains the teleoperation module used by the simulator. The main frontend launches the module, in observer mode, only while the simulation is running. Observer mode is important for CARLA-SUMO-Artery scenarios because it avoids adding, deleting, or taking ownership of simulation vehicles.

To wire this into an AV scenario, raise a scenario-level intervention event when the AV stack enters an uncertain state, for example low detector confidence, inconsistent localization, or a safety monitor warning. The frontend or orchestrator can then call the teleoperation start API to open the local teleop UI and let the operator inspect the fleet and select a vehicle for intervention.
