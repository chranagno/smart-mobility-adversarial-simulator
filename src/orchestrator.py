#!/usr/bin/env python
"""
Simulator orchestrator with Artery integration.
Runs CARLA-SUMO co-simulation and synchronizes optional V2X services.
"""

import argparse
import logging
import time
import os
os.environ["DISPLAY"] = ""
import matplotlib
matplotlib.use('Agg')
import sys
import yaml
import asyncio
import websockets
import json
import threading
import math
import random
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import numpy as np

# Add local simulator modules to path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)

# Add parent directory to path so we can import modules as a package
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

# Ensure the local modules directory is importable directly.
MODULES_DIR = os.path.join(CURRENT_DIR, 'modules')
if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)

# Add carla/Co-Simulation/Sumo to path for co_simulation imports
CARLA_COSIM_DIR = os.path.join(CURRENT_DIR, 'carla', 'Co-Simulation', 'Sumo')
if CARLA_COSIM_DIR not in sys.path:
    sys.path.append(CARLA_COSIM_DIR)

# Add sumo_integration to path
_sumo_integration_path = os.path.join(CARLA_COSIM_DIR, 'sumo_integration')
if _sumo_integration_path not in sys.path:
    sys.path.append(_sumo_integration_path)

# Create modules.co_simulation module structure.
# The co-simulation helpers live in carla/Co-Simulation/Sumo.
import types

# Ensure modules is recognized as a package before creating module structures.
# This must happen before any imports that use modules
modules_path = os.path.join(CURRENT_DIR, 'modules')
if os.path.isdir(modules_path):
    # Create modules as a proper package with __path__
    if 'modules' not in sys.modules:
        modules_module = types.ModuleType('modules')
        modules_module.__path__ = [modules_path]
        modules_module.__file__ = os.path.join(modules_path, '__init__.py')
        sys.modules['modules'] = modules_module

def create_module_structure(base_name):
    """Create the runtime module structure for CARLA/SUMO integration imports."""
    # Split the base name to create nested modules
    parts = base_name.split('.')
    
    # Create all parent modules
    current_path = ''
    for part in parts:
        if current_path:
            current_path += '.' + part
        else:
            current_path = part
        
        if current_path not in sys.modules:
            module = types.ModuleType(current_path)
            # Set __path__ for packages to make them proper packages
            if current_path == 'modules':
                modules_path = os.path.join(CURRENT_DIR, 'modules')
                if os.path.isdir(modules_path):
                    module.__path__ = [modules_path]
                    module.__file__ = os.path.join(modules_path, '__init__.py')
            sys.modules[current_path] = module
    
    # Create co_simulation module
    co_sim_name = f'{base_name}.co_simulation'
    if co_sim_name not in sys.modules:
        co_sim_module = types.ModuleType(co_sim_name)
        # Make it a package by setting __path__
        co_sim_module.__path__ = []
        sys.modules[co_sim_name] = co_sim_module
    
    # Create sumo_integration module
    sumo_int_name = f'{base_name}.co_simulation.sumo_integration'
    if sumo_int_name not in sys.modules:
        sumo_int_module = types.ModuleType(sumo_int_name)
        # Make it a package by setting __path__
        sumo_int_module.__path__ = []
        sys.modules[sumo_int_name] = sumo_int_module
    
    return sys.modules[sumo_int_name]

_sumo_int_module_modules = create_module_structure('modules')

# Import the actual modules and add them to the mock structure
# Import constants first (it doesn't require traci)
constants = None
bridge_helper = None
sumo_simulation = None

try:
    # Constants doesn't require traci, so import it first
    import sumo_integration.constants as constants  # noqa: E402
except ImportError as e:
    print(f"Warning: Could not import sumo_integration.constants: {e}")

# Try to import bridge_helper and sumo_simulation (they require traci)
try:
    import sumo_integration.bridge_helper as bridge_helper  # noqa: E402
except ImportError as e:
    print(f"Warning: Could not import sumo_integration.bridge_helper: {e}")
    print("This is expected if SUMO/traci is not available. BridgeHelper will not be available.")

try:
    import sumo_integration.sumo_simulation as sumo_simulation  # noqa: E402
except ImportError as e:
    print(f"Warning: Could not import sumo_integration.sumo_simulation: {e}")
    print("This is expected if SUMO/traci is not available. SumoSimulation will not be available.")

def populate_module_structure(base_name, sumo_int_module):
    """Populate a module structure with the actual imports."""
    # Expose constants module (always try to populate this)
    if constants is not None:
        sumo_int_module.constants = constants
        sumo_int_module.SPAWN_OFFSET_Z = getattr(constants, 'SPAWN_OFFSET_Z', 0.5)
        sumo_int_module.INVALID_ACTOR_ID = getattr(constants, 'INVALID_ACTOR_ID', -1)
        
        # Make constants available as a submodule
        constants_name = f'{base_name}.co_simulation.sumo_integration.constants'
        _constants_module = types.ModuleType(constants_name)
        for attr in dir(constants):
            if not attr.startswith('_'):
                setattr(_constants_module, attr, getattr(constants, attr))
        sys.modules[constants_name] = _constants_module
    else:
        # Create a minimal constants module with default values
        constants_name = f'{base_name}.co_simulation.sumo_integration.constants'
        _constants_module = types.ModuleType(constants_name)
        _constants_module.SPAWN_OFFSET_Z = 0.5
        _constants_module.INVALID_ACTOR_ID = -1
        sys.modules[constants_name] = _constants_module
        sumo_int_module.SPAWN_OFFSET_Z = 0.5
        sumo_int_module.INVALID_ACTOR_ID = -1
    
    # Expose bridge_helper if available, otherwise create a stub
    bridge_helper_name = f'{base_name}.co_simulation.sumo_integration.bridge_helper'
    if bridge_helper is not None:
        sumo_int_module.bridge_helper = bridge_helper
        sumo_int_module.BridgeHelper = bridge_helper.BridgeHelper
        
        # Make bridge_helper available as a submodule
        _bridge_helper_module = types.ModuleType(bridge_helper_name)
        _bridge_helper_module.BridgeHelper = bridge_helper.BridgeHelper
        for attr in dir(bridge_helper):
            if not attr.startswith('_'):
                setattr(_bridge_helper_module, attr, getattr(bridge_helper, attr))
        sys.modules[bridge_helper_name] = _bridge_helper_module
    else:
        # Create a stub BridgeHelper class for imports to work
        class StubBridgeHelper:
            blueprint_library = None
            offset = None
            @staticmethod
            def get_carla_blueprint(*args, **kwargs):
                return None
            @staticmethod
            def get_carla_transform(*args, **kwargs):
                return None
            @staticmethod
            def get_sumo_vtype(*args, **kwargs):
                return None
            @staticmethod
            def get_sumo_transform(*args, **kwargs):
                return None
            @staticmethod
            def get_sumo_traffic_light_state(*args, **kwargs):
                return None
        
        _bridge_helper_module = types.ModuleType(bridge_helper_name)
        _bridge_helper_module.BridgeHelper = StubBridgeHelper
        sys.modules[bridge_helper_name] = _bridge_helper_module
        sumo_int_module.bridge_helper = _bridge_helper_module
        sumo_int_module.BridgeHelper = StubBridgeHelper
    
    # Expose sumo_simulation if available, otherwise create a stub
    sumo_sim_name = f'{base_name}.co_simulation.sumo_integration.sumo_simulation'
    if sumo_simulation is not None:
        sumo_int_module.sumo_simulation = sumo_simulation
        sumo_int_module.SumoSimulation = sumo_simulation.SumoSimulation
        
        # Make sumo_simulation available as a submodule
        _sumo_sim_module = types.ModuleType(sumo_sim_name)
        _sumo_sim_module.SumoSimulation = sumo_simulation.SumoSimulation
        for attr in dir(sumo_simulation):
            if not attr.startswith('_'):
                setattr(_sumo_sim_module, attr, getattr(sumo_simulation, attr))
        sys.modules[sumo_sim_name] = _sumo_sim_module
    else:
        # Create a stub SumoSimulation class for imports to work
        class StubSumoSimulation:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("SumoSimulation is not available. SUMO/traci is required for co-simulation.")
            def tick(self):
                pass
            def close(self):
                pass
        
        _sumo_sim_module = types.ModuleType(sumo_sim_name)
        _sumo_sim_module.SumoSimulation = StubSumoSimulation
        sys.modules[sumo_sim_name] = _sumo_sim_module
        sumo_int_module.sumo_simulation = _sumo_sim_module
        sumo_int_module.SumoSimulation = StubSumoSimulation

populate_module_structure('modules', _sumo_int_module_modules)

# CARLA imports
import glob
try:
    sys.path.append(
        glob.glob(f'{os.environ["CARLA_HOME"]}/PythonAPI/carla/dist/carla-*%d.%d-%s.egg' %
                  (sys.version_info.major, sys.version_info.minor,
                   'win-amd64' if os.name == 'nt' else 'linux-x86_64'))[0])
except (IndexError, KeyError):
    pass
import carla

# Local simulator module imports.
import modules.scenario.cosim_api as cosim_api
import modules.scenario.sim_api as sim_api
from modules.common.cav_world import CavWorld
from modules.scenario.yaml_utils import add_current_time
from modules.evaluation.evaluate_manager import EvaluationManager

# Artery integration
from carla_artery_connection import ArterySynchronization
from vehicle_position_tracker import VehiclePositionTracker
from vehicle_map_plotter import VehicleMapPlotter
from python_artery_client.artery_control_client import ArteryControlClient

# Ego sensor recording
from ego_sensor_manager import EgoSensorManager, EgoRecorder

# Adversarial attack module (optional — graceful fallback if not installed)
try:
    _ATTACK_MODULE_DIR = os.path.join(os.path.dirname(CURRENT_DIR), 'adversarial_attack_module')
    if _ATTACK_MODULE_DIR not in sys.path:
        sys.path.insert(0, _ATTACK_MODULE_DIR)
    from attack_client import AttackOrchestrator
    _HAS_ATTACK_MODULE = True
except ImportError as _attack_import_err:
    AttackOrchestrator = None
    _HAS_ATTACK_MODULE = False
    logging.debug('[Import] Adversarial attack module not available: %s', _attack_import_err)


def deep_merge_dict(base_dict, override_dict):
    """
    Deep merge two dictionaries.
    Values from override_dict take precedence over base_dict.
    Nested dictionaries are merged recursively.
    
    Args:
        base_dict: Base dictionary (general config)
        override_dict: Override dictionary (scenario-specific config)
    
    Returns:
        Merged dictionary
    """
    result = base_dict.copy()
    
    for key, value in override_dict.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dictionaries
            result[key] = deep_merge_dict(result[key], value)
        else:
            # Override with scenario-specific value
            result[key] = value
    
    return result


def load_merged_config(general_config_path, scenario_config_path):
    """
    Load and merge general config with scenario-specific config.
    
    Args:
        general_config_path: Path to general configuration YAML file
        scenario_config_path: Path to scenario-specific configuration YAML file
    
    Returns:
        Merged configuration dictionary
    """
    # Load general config
    general_config = {}
    if general_config_path and os.path.isfile(general_config_path):
        print(f"[Config] Loading general config from: {general_config_path}")
        with open(general_config_path, 'r', encoding='utf-8') as fh:
            general_config = yaml.safe_load(fh) or {}
    else:
        print(f"[Config] General config not found at {general_config_path}, using defaults")
    
    # Load scenario-specific config
    if not scenario_config_path or not os.path.isfile(scenario_config_path):
        raise FileNotFoundError(f"Scenario config not found: {scenario_config_path}")
    
    print(f"[Config] Loading scenario config from: {scenario_config_path}")
    with open(scenario_config_path, 'r', encoding='utf-8') as fh:
        scenario_config = yaml.safe_load(fh) or {}
    
    # Merge configs (scenario config overrides general config)
    merged_config = deep_merge_dict(general_config, scenario_config)
    
    print(f"[Config] Configuration merged successfully")
    return merged_config


def _parse_sumocfg(cfg_path):
    """Parse SUMO .sumocfg and return (net_file, route_files list)."""
    tree = ET.parse(cfg_path)
    root = tree.getroot()
    net_file = None
    route_files = []
    for input_elem in root.findall('input'):
        for child in input_elem:
            if child.tag == 'net-file':
                net_file = child.get('value')
            if child.tag == 'route-files':
                value = child.get('value') or ''
                route_files.extend([p.strip() for p in value.split(',') if p.strip()])
    return net_file, route_files


def _load_lane_shapes(net_file):
    """Return dict edge_id -> first lane shape (list of (x,y))."""
    tree = ET.parse(net_file)
    root = tree.getroot()
    shapes = {}
    for edge in root.findall('edge'):
        edge_id = edge.get('id')
        if not edge_id or edge.get('function') == 'internal':
            continue
        lane = edge.find('lane')
        if lane is None:
            continue
        shape = lane.get('shape')
        if not shape:
            continue
        pts = []
        for pt in shape.split():
            try:
                x_str, y_str = pt.split(',')
                pts.append((float(x_str), float(y_str)))
            except ValueError:
                continue
        if pts:
            shapes[edge_id] = pts
    return shapes


def _collect_routes_from_rou(route_file, lane_shapes):
    """Yield (name, first_pt, second_pt, last_pt) for each vehicle/flow in rou.xml."""
    tree = ET.parse(route_file)
    root = tree.getroot()
    # Build route definitions
    route_defs = {}
    for rte in root.findall('route'):
        rid = rte.get('id')
        edges = (rte.get('edges') or '').split()
        if rid and edges:
            route_defs[rid] = edges

    def edges_for(elem):
        if elem.get('edges'):
            return elem.get('edges').split()
        rid = elem.get('route')
        if rid and rid in route_defs:
            return route_defs[rid]
        return []

    for elem in list(root.findall('vehicle')) + list(root.findall('flow')):
        vid = elem.get('id') or elem.get('name') or f"auto_{len(route_defs)}"
        edges = edges_for(elem)
        if len(edges) == 0:
            continue
        first_shape = lane_shapes.get(edges[0])
        last_shape = lane_shapes.get(edges[-1])
        if not first_shape or not last_shape:
            continue
        first_pt = first_shape[0]
        last_pt = last_shape[-1]
        second_pt = first_shape[1] if len(first_shape) > 1 else None
        yield vid, first_pt, second_pt, last_pt


def _calc_yaw(p0, p1):
    if not p0 or not p1:
        return 0.0
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    return math.degrees(math.atan2(dy, dx))


class Orchestrator:
    """
    Orchestrator using the local co-simulation manager with Artery synchronization.
    Supports both CARLA-only and CARLA-SUMO co-simulation modes.
    """
    
    def __init__(self, args):
        """Initialize the orchestrator with local modules and Artery integration."""
        print("[Orchestrator] Initializing...")
        self.args = args
        self.cams = []
        
        # Load and merge general config with scenario-specific config
        general_config_path = getattr(args, 'general_config', None)
        scenario_config_path = getattr(args, 'scenario_config', None)
        
        # If general_config not provided, look for it in the same directory as scenario config
        if not general_config_path and scenario_config_path:
            config_dir = os.path.dirname(os.path.abspath(scenario_config_path))
            default_general_config = os.path.join(config_dir, 'general_config.yaml')
            if os.path.isfile(default_general_config):
                general_config_path = default_general_config
        
        # Load and merge configurations
        self.scenario_params = load_merged_config(general_config_path, scenario_config_path)
        
        # Extract parameters from YAML and override args
        # YAML is the source of truth for these parameters
        carla_config = self.scenario_params.get('carla', {})
        world_config = self.scenario_params.get('world', {})
        sumo_config = self.scenario_params.get('sumo', {})
        
        # Override args with YAML values (YAML takes precedence)
        if carla_config.get('host'):
            args.carla_host = carla_config['host']
        if carla_config.get('port'):
            args.carla_port = carla_config['port']
        if world_config.get('town'):
            args.carla_town = world_config['town']
        
        # Extract SUMO config file path from YAML if available
        if sumo_config.get('config'):
            args.sumo_cfg_file = sumo_config['config']
            # Extract parent path from config file path
            if args.sumo_cfg_file and not args.sumo_file_parent_path:
                args.sumo_file_parent_path = os.path.dirname(os.path.abspath(args.sumo_cfg_file))
        
        # Add current time to scenario params for runtime output naming.
        self.scenario_params = add_current_time(self.scenario_params)

        # Recording/data dump options: sensors, semantic lidar, and ground truth.
        recording_cfg = self.scenario_params.get('recording', {})
        # recording_available = sensors will be spawned (from YAML config)
        self.recording_available = bool(recording_cfg.get('data_dump', False))
        # data_dump = active disk writing (only from CLI --data-dump flag or Record button)
        self.data_dump = bool(getattr(args, 'data_dump', False))
        self.include_ground_truth = recording_cfg.get('include_ground_truth', self.recording_available)
        self.ground_truth_cfg = recording_cfg.get('ground_truth', {}) or {}
        self.gt_detections_enabled = bool(
            self.ground_truth_cfg.get('detections', {}).get('enabled', False)
        )
        self.gt_detections_debug_draw = bool(
            self.ground_truth_cfg.get('detections', {}).get('debug_draw', False)
        )
        self.gt_detection_categories = self.ground_truth_cfg.get(
            'detections', {}
        ).get('categories', ['vehicle'])
        segmentation_cfg = self.ground_truth_cfg.get('segmentation', {}) or {}
        self.gt_camera_segmentation_enabled = bool(
            segmentation_cfg.get('camera', {}).get('enabled', False)
        )
        self.gt_lidar_segmentation_enabled = bool(
            segmentation_cfg.get('lidar', {}).get('enabled', False)
        )
        data_dump_dir = recording_cfg.get('data_dump_dir')
        if data_dump_dir:
            os.environ['DATA_DUMP_DIR'] = data_dump_dir
            print(f"[Orchestrator] DATA_DUMP_DIR set from config: {data_dump_dir}")
        if self.recording_available:
            print("[Orchestrator] Recording infrastructure enabled (sensors will be spawned).")
        if self.data_dump:
            print("[Orchestrator] Data dump active from CLI (auto-recording on start).")
        
        # Optionally auto-generate CAVs from SUMO route file
        self._maybe_autofill_cavs_from_sumo_routes()
        
        # Determine simulation mode
        self.use_cosim = 'sumo' in self.scenario_params and self.scenario_params.get('sumo') is not None
        self.town = self.scenario_params.get('world', {}).get('town') or args.carla_town or 'Town01'
        
        # Create shared CAV world state.
        apply_ml = getattr(args, 'apply_ml', False)
        self.cav_world = CavWorld(apply_ml)
        
        # Initialize Artery if requested
        self.artery_conn = None
        self.artery_sync_wrapper = None  # Wrapper to provide net access for Artery
        if getattr(args, 'start_artery', False):
            self.artery_conn = ArterySynchronization()
            # Create a wrapper object that provides 'net' attribute for Artery
            # This will be set after scenario manager is initialized
            print("[Orchestrator] Artery synchronization enabled")
        
        # Initialize Artery ExternalControl if requested
        self.artery_controller = None
        if getattr(args, 'artery_control', False):
            try:
                self.artery_controller = ArteryControlClient(
                    host=getattr(args, 'artery_host', '127.0.0.1'),
                    port=getattr(args, 'artery_port', 8888),
                )
                logging.info(f"[Artery] ExternalControl bridge enabled")
            except Exception as exc:
                logging.warning(f"[Artery] Failed to initialize controller: {exc}")
        
        # Initialize local scenario manager.
        if self.use_cosim:
            # CARLA-SUMO co-simulation mode
            self._init_cosim_scenario_manager(args)
        else:
            # CARLA-only mode
            self._init_carla_scenario_manager(args)
        
        # Initialize position tracking
        tracked_vehicles = getattr(args, 'track_vehicles', None)
        self.position_tracker = VehiclePositionTracker(
            tracked_vehicle_ids=tracked_vehicles.split(',') if tracked_vehicles else None,
            output_file='vehicle_positions.json',
            verbose=False
        )
        
        # Initialize map plotter
        enable_map = getattr(args, 'enable_map', False)
        self.map_plotter = None
        if enable_map:
            use_gps = getattr(args, 'map_use_gps', False)
            update_interval = getattr(args, 'map_update_interval', 1.0)
            self.map_plotter = VehicleMapPlotter(
                update_interval=update_interval,
                use_gps=use_gps,
                max_history=100
            )
            self.map_plotter.start_plotting(blocking=False)
        
        # Initialize WebSocket server
        self.ws_port = getattr(args, 'ws_port', 8765)
        self.ws_clients = set()
        self.ws_server = None
        self.ws_loop = None
        self.ws_thread = None
        enable_ws = getattr(args, 'enable_ws', False)
        if enable_ws:
            self.start_websocket_server()
            print(f"[Orchestrator] WebSocket server started on port {self.ws_port}")
        
        # Thread pool for concurrent operations
        self.step_executor = ThreadPoolExecutor(max_workers=2)
        
        # Vehicle managers created by the local scenario manager.
        self.vehicle_managers = []
        self._create_vehicles()
        
        # Evaluation manager
        self.eval_manager = None
        if getattr(args, 'enable_evaluation', False):
            self.eval_manager = EvaluationManager(
                self.scenario_manager.cav_world,
                script_name='modules_orchestrator',
                current_time=self.scenario_params['current_time']
            )
        
        # --- Multi-vehicle sensor recording ---
        recording_cfg = self.scenario_params.get('recording', {})
        self.apply_to_all_cavs = recording_cfg.get('apply_to_all_cavs', False)
        self.max_record_vehicles = recording_cfg.get('max_vehicles', 0)  # 0 = all

        ego_cfg = self.scenario_params.get('ego', {})
        selection_cfg = ego_cfg.get('selection', {}) or {}
        self.ego_selection_mode = selection_cfg.get('mode')
        self.ego_selection_seed = selection_cfg.get(
            'seed',
            self.scenario_params.get('world', {}).get('seed')
        )
        self.ego_target_sumo_id = selection_cfg.get('sumo_id', ego_cfg.get('sumo_id'))
        self.ego_target_carla_id = selection_cfg.get('carla_id', ego_cfg.get('carla_id'))

        # Build sensor configs for ego and non-ego vehicles separately.
        self._other_vehicle_sensor_configs = self._build_sensor_configs_from_perception(
            self._get_other_vehicle_perception_cfg()
        )
        self._ego_vehicle_sensor_configs = self._build_ego_sensor_configs()
        self._other_vehicle_sensor_configs = self._apply_ground_truth_sensor_expansions(
            self._other_vehicle_sensor_configs
        )
        self._ego_vehicle_sensor_configs = self._apply_ground_truth_sensor_expansions(
            self._ego_vehicle_sensor_configs
        )
        
        # Per-vehicle recorders: sumo_id -> (EgoSensorManager, EgoRecorder)
        self.vehicle_recorders = {}
        self.vehicles_attached = False
        self.record_tick_count = 0
        self.active_recording_save_time = None
        self.sim_start_time = time.time()
        
        # Keep resolved ego ids for runtime/UI use
        self.ego_sumo_id = self.ego_target_sumo_id
        self.ego_carla_id = self.ego_target_carla_id
        
        print(f"[Orchestrator] Recording config: apply_to_all={self.apply_to_all_cavs}, "
              f"other_vehicle sensors={len(self._other_vehicle_sensor_configs)}, "
              f"ego sensors={len(self._ego_vehicle_sensor_configs)}, "
              f"gt_detections={self.gt_detections_enabled}, "
              f"gt_debug_draw={self.gt_detections_debug_draw}, "
              f"gt_seg_camera={self.gt_camera_segmentation_enabled}, "
              f"gt_seg_lidar={self.gt_lidar_segmentation_enabled}, "
              f"ego_selection={self.ego_selection_mode or 'first'}, "
              f"data_dump={self.data_dump}")
        
        # --- Adversarial attack module ---
        self.attack_orchestrator = None
        if _HAS_ATTACK_MODULE and AttackOrchestrator is not None:
            self.attack_orchestrator = AttackOrchestrator.from_config(self.scenario_params)
            if self.attack_orchestrator:
                logging.info(
                    '[Orchestrator] Attack module initialised (server=%s, '
                    '3d_attacks=%d, 2d_attacks=%d)',
                    self.attack_orchestrator.cfg.server_url,
                    len(self.attack_orchestrator.cfg.attacks_3d),
                    len(self.attack_orchestrator.cfg.attacks_2d),
                )
            else:
                logging.info('[Orchestrator] Attack module not configured in scenario YAML')
        else:
            logging.debug('[Orchestrator] Attack module not available (import failed)')
        self._last_attack_server_retry_time = 0.0

        print("[Orchestrator] Initialization complete")
    
    def _init_cosim_scenario_manager(self, args):
        """Initialize CoScenarioManager for CARLA-SUMO co-simulation."""
        print("[Orchestrator] Initializing CARLA-SUMO co-simulation...")
        
        # Get SUMO file path
        sumo_config = self.scenario_params.get('sumo', {})
        sumo_file_parent_path = getattr(args, 'sumo_file_parent_path', None)
        sumo_cfg_file = getattr(args, 'sumo_cfg_file', None) or sumo_config.get('sumo_cfg_file')
     
        if not sumo_file_parent_path:
            # Try to infer from sumo config or use default
            if sumo_cfg_file:
                sumo_file_parent_path = os.path.dirname(sumo_cfg_file)
                base_name = os.path.splitext(os.path.basename(sumo_cfg_file))[0]
                sumo_file_parent_path = os.path.join(sumo_file_parent_path, base_name)
            else:
                # Fall back to the repo SUMO configs when the scenario omitted a path.
                sumo_file_parent_path = os.path.join(
                    os.path.dirname(CURRENT_DIR), 'sumo_configs'
                )
                sumo_cfg_file = os.path.join(sumo_file_parent_path, f'{self.town}.sumocfg')
        
        # Get XODR path if using custom map
        xodr_path = getattr(args, 'xodr_path', None)
        
        # CARLA version
        carla_version = getattr(args, 'carla_version', '0.9.15')
        apply_ml = getattr(args, 'apply_ml', False)
        
        # Create CoScenarioManager
        self.scenario_manager = cosim_api.CoScenarioManager(
            self.scenario_params,
            apply_ml,
            carla_version,
            xodr_path=xodr_path,
            town=self.town if not xodr_path else None,
            cav_world=self.cav_world,
            sumo_cfg_file=sumo_cfg_file
        )
        
        self.world = self.scenario_manager.world
        
        # Create Artery sync wrapper with SUMO network access
        if self.artery_conn:
            class ArterySyncWrapper:
                """Wrapper to provide net access for Artery integration."""
                def __init__(self, scenario_manager):
                    self.scenario_manager = scenario_manager
                    # Artery expects artery2sumo_ids mapping
                    self.artery2sumo_ids = {}
                    self.arteryAttackers_ids = set()
                
                @property
                def net(self):
                    """Provide SUMO network for Artery coordinate conversion."""
                    if hasattr(self.scenario_manager, 'sumo') and hasattr(self.scenario_manager.sumo, 'net'):
                        return self.scenario_manager.sumo.net
                    return None
            
            self.artery_sync_wrapper = ArterySyncWrapper(self.scenario_manager)
        
        print(f"[Orchestrator] CoScenarioManager initialized for {self.town}")
    
    def _maybe_autofill_cavs_from_sumo_routes(self):
        """If enabled in scenario config, create CAV entries from SUMO routes."""
        scenario_cfg = self.scenario_params.get('scenario', {})
        auto_cfg = scenario_cfg.get('auto_cavs_from_sumo_routes') or {}
        if not auto_cfg:
            return
        enabled = auto_cfg is True or auto_cfg.get('enabled')
        if not enabled:
            return

        sumo_cfg = self.scenario_params.get('sumo', {}) or {}
        sumo_cfg_path = sumo_cfg.get('sumo_cfg_file') or sumo_cfg.get('config')
        if not sumo_cfg_path or not os.path.isfile(sumo_cfg_path):
            print("[Orchestrator] auto_cavs_from_sumo_routes enabled but no valid sumo_cfg_file/config found")
            return

        cfg_dir = os.path.dirname(os.path.abspath(sumo_cfg_path))
        net_file, route_files = _parse_sumocfg(sumo_cfg_path)
        if not net_file:
            print("[Orchestrator] SUMO config missing net-file; cannot auto-generate CAVs")
            return
        net_path = net_file if os.path.isabs(net_file) else os.path.join(cfg_dir, net_file)
        route_paths = []
        for rf in route_files:
            route_paths.append(rf if os.path.isabs(rf) else os.path.join(cfg_dir, rf))

        if not os.path.isfile(net_path):
            print(f"[Orchestrator] Net file not found: {net_path}")
            return
        if not route_paths:
            print("[Orchestrator] No route-files listed in sumo_cfg; nothing to auto-generate")
            return

        lane_shapes = _load_lane_shapes(net_path)
        cavs = []
        limit = auto_cfg.get('limit')
        for route_file in route_paths:
            if not os.path.isfile(route_file):
                print(f"[Orchestrator] Route file not found: {route_file}")
                continue
            for vid, p0, p1, pend in _collect_routes_from_rou(route_file, lane_shapes):
                yaw = _calc_yaw(p0, p1 or pend)
                cavs.append({
                    'name': vid,
                    'spawn_position': [p0[0], p0[1], 0.3, 0, 0, yaw],
                    'destination': [pend[0], pend[1], 0.3],
                    'behavior': {
                        'local_planner': {'debug_trajectory': False, 'debug': False}
                    }
                })
                if limit and len(cavs) >= limit:
                    break
            if limit and len(cavs) >= limit:
                break

        if not cavs:
            print("[Orchestrator] No CAVs generated from SUMO routes")
            return

        scenario_cfg.setdefault('single_cav_list', [])
        scenario_cfg['single_cav_list'].extend(cavs)
        self.scenario_params['scenario'] = scenario_cfg
        print(f"[Orchestrator] Added {len(cavs)} CAVs from SUMO routes")
    
    def _init_carla_scenario_manager(self, args):
        """Initialize ScenarioManager for CARLA-only simulation."""
        print("[Orchestrator] Initializing CARLA-only simulation...")
        
        xodr_path = getattr(args, 'xodr_path', None)
        carla_version = getattr(args, 'carla_version', '0.9.15')
        apply_ml = getattr(args, 'apply_ml', False)
        
        self.scenario_manager = sim_api.ScenarioManager(
            self.scenario_params,
            apply_ml,
            carla_version,
            xodr_path=xodr_path,
            town=self.town if not xodr_path else None,
            cav_world=self.cav_world
        )
        
        self.world = self.scenario_manager.world
        
        # Create CARLA traffic if configured
        if 'carla_traffic_manager' in self.scenario_params:
            traffic_manager, bg_veh_list = self.scenario_manager.create_traffic_carla()
            print(f"[Orchestrator] Created {len(bg_veh_list)} background vehicles")
        
        print(f"[Orchestrator] ScenarioManager initialized for {self.town}")
    
    def _create_vehicles(self):
        """Create CAVs using the local vehicle manager creation."""
        scenario = self.scenario_params.get('scenario', {})
        
        # Create single CAVs from scenario.single_cav_list in YAML.
        if 'single_cav_list' in scenario and scenario['single_cav_list']:
            print(f"[Orchestrator] Creating {len(scenario['single_cav_list'])} single CAVs...")
            try:
                single_cavs = self.scenario_manager.create_vehicle_manager(
                    application=['single'],
                    data_dump=self.data_dump
                )
                if single_cavs:
                    self.vehicle_managers.extend(single_cavs)
                    print(f"[Orchestrator] Created {len(single_cavs)} single CAVs")
            except Exception as e:
                print(f"[Orchestrator] Error creating single CAVs: {e}")
        
        print(f"[Orchestrator] Total vehicle managers created: {len(self.vehicle_managers)}")
    
    # ------------------------------------------------------------------
    # Sensor config builder from vehicle_base YAML
    # ------------------------------------------------------------------
    def _get_other_vehicle_perception_cfg(self):
        other_cfg = self.scenario_params.get('other_vehicles', {}) or {}
        other_perception = other_cfg.get('sensing', {}).get('perception')
        if other_perception:
            return other_perception
        return self.scenario_params.get('vehicle_base', {}).get('sensing', {}).get('perception', {})

    def _build_sensor_configs_from_perception(self, perception):
        """
        Convert a sensing.perception config into a list of sensor config
        dicts suitable for EgoSensorManager.spawn_sensors().
        """
        perception = perception or {}
        cam_cfg = perception.get('camera', {})
        lidar_cfg = perception.get('lidar', {})
        
        configs = []
        
        # --- RGB Cameras ---
        cam_positions = cam_cfg.get('positions', [])
        cam_names = ['front', 'left', 'right', 'rear']
        for i, pos in enumerate(cam_positions):
            # pos = [x, y, z, yaw]  (roll/pitch default 0)
            x = float(pos[0]) if len(pos) > 0 else 0.0
            y = float(pos[1]) if len(pos) > 1 else 0.0
            z = float(pos[2]) if len(pos) > 2 else 0.0
            yaw = float(pos[3]) if len(pos) > 3 else 0.0
            name = cam_names[i] if i < len(cam_names) else f'cam{i}'
            configs.append({
                'id': f'rgb_{name}',
                'type': 'sensor.camera.rgb',
                'transform': {'x': x, 'y': y, 'z': z, 'yaw': yaw},
                'attributes': {
                    'image_size_x': '800',
                    'image_size_y': '600',
                    'fov': '100',
                }
            })
        
        # --- LiDAR ---
        if lidar_cfg:
            configs.append({
                'id': 'lidar_top',
                'type': 'sensor.lidar.ray_cast',
                'transform': {'x': 0.0, 'y': 0.0, 'z': 2.5},
                'attributes': {
                    'channels': str(lidar_cfg.get('channels', 32)),
                    'range': str(lidar_cfg.get('range', 120)),
                    'points_per_second': str(lidar_cfg.get('points_per_second', 1000000)),
                    'rotation_frequency': str(lidar_cfg.get('rotation_frequency', 20)),
                    'upper_fov': str(lidar_cfg.get('upper_fov', 10.0)),
                    'lower_fov': str(lidar_cfg.get('lower_fov', -30.0)),
                    'dropoff_general_rate': str(lidar_cfg.get('dropoff_general_rate', 0.0)),
                    'dropoff_intensity_limit': str(lidar_cfg.get('dropoff_intensity_limit', 1.0)),
                    'dropoff_zero_intensity': str(lidar_cfg.get('dropoff_zero_intensity', 0.0)),
                    'noise_stddev': str(lidar_cfg.get('noise_stddev', 0.0)),
                }
            })
        
        return configs

    def _build_ego_sensor_configs(self):
        ego_cfg = self.scenario_params.get('ego', {}) or {}
        if ego_cfg.get('sensors'):
            return ego_cfg['sensors']

        ego_perception = ego_cfg.get('sensing', {}).get('perception')
        if ego_perception:
            return self._build_sensor_configs_from_perception(ego_perception)

        return list(self._other_vehicle_sensor_configs)

    def _apply_ground_truth_sensor_expansions(self, sensor_configs):
        expanded = list(sensor_configs or [])
        extra_configs = []

        if self.gt_camera_segmentation_enabled:
            for cfg in expanded:
                if cfg.get('type') != 'sensor.camera.rgb':
                    continue
                seg_cfg = {
                    'id': f"{cfg['id']}_instseg",
                    'type': 'sensor.camera.instance_segmentation',
                    'transform': dict(cfg.get('transform', {})),
                    'attributes': dict(cfg.get('attributes', {})),
                }
                extra_configs.append(seg_cfg)

        if self.gt_lidar_segmentation_enabled:
            for cfg in expanded:
                if cfg.get('type') != 'sensor.lidar.ray_cast':
                    continue
                sem_cfg = {
                    'id': f"{cfg['id']}_semantic",
                    'type': 'sensor.lidar.ray_cast_semantic',
                    'transform': dict(cfg.get('transform', {})),
                    'attributes': dict(cfg.get('attributes', {})),
                }
                extra_configs.append(sem_cfg)

        expanded.extend(extra_configs)
        return expanded

    def _resolve_ego_sumo_id(self, mapping):
        if not mapping:
            return None

        if self.ego_sumo_id is not None and str(self.ego_sumo_id) in mapping:
            chosen = str(self.ego_sumo_id)
            self.ego_carla_id = mapping[chosen]
            return chosen

        available_ids = sorted((str(k) for k in mapping.keys()), key=str)
        mode = self.ego_selection_mode
        if not mode:
            if self.ego_target_sumo_id is not None:
                mode = 'sumo_id'
            elif self.ego_target_carla_id is not None:
                mode = 'carla_id'
            else:
                mode = 'first'

        chosen = None
        if mode == 'sumo_id':
            candidate = str(self.ego_target_sumo_id)
            if candidate in mapping:
                chosen = candidate
            else:
                logging.warning('[Recording] ego.selection.sumo_id=%s not found; falling back to first available vehicle', candidate)
        elif mode == 'carla_id':
            try:
                candidate = int(self.ego_target_carla_id)
                for sumo_id, carla_id in mapping.items():
                    if int(carla_id) == candidate:
                        chosen = str(sumo_id)
                        break
                if chosen is None:
                    logging.warning('[Recording] ego.selection.carla_id=%s not found; falling back to first available vehicle', candidate)
            except (TypeError, ValueError):
                logging.warning('[Recording] Invalid ego.selection.carla_id=%s; falling back to first available vehicle', self.ego_target_carla_id)
        elif mode == 'random':
            rng = random.Random(self.ego_selection_seed)
            chosen = rng.choice(available_ids)
        else:
            chosen = available_ids[0]

        if chosen is None:
            chosen = available_ids[0]

        self.ego_sumo_id = str(chosen)
        self.ego_carla_id = mapping[self.ego_sumo_id]
        return self.ego_sumo_id

    def _select_recorded_vehicle_ids(self, mapping):
        vehicle_ids = sorted((str(k) for k in mapping.keys()), key=str)
        if not vehicle_ids:
            return []

        ego_sumo_id = self._resolve_ego_sumo_id(mapping)
        selected_ids = []
        if ego_sumo_id in vehicle_ids:
            selected_ids.append(ego_sumo_id)

        for sumo_id in vehicle_ids:
            if sumo_id == ego_sumo_id:
                continue
            selected_ids.append(sumo_id)

        if self.max_record_vehicles > 0:
            selected_ids = selected_ids[:self.max_record_vehicles]

        return selected_ids

    # ------------------------------------------------------------------
    # Multi-vehicle sensor attachment
    # ------------------------------------------------------------------
    def _attach_sensors_to_vehicles(self):
        """
        Attach sensors to SUMO-synced vehicles based on vehicle_base config.
        Creates an EgoSensorManager + EgoRecorder per vehicle.
        """
        if not self.use_cosim:
            return
        mapping = self.scenario_manager.sumo2carla_ids
        if not mapping:
            return
        
        if not self._other_vehicle_sensor_configs and not self._ego_vehicle_sensor_configs:
            print('[Orchestrator] No sensor configs for recording, skipping attachment')
            return

        vehicle_ids = self._select_recorded_vehicle_ids(mapping)
        
        save_time = self.active_recording_save_time or self.scenario_params.get(
            'current_time',
            time.strftime('%Y_%m_%d_%H_%M_%S')
        )
        
        for sumo_id in vehicle_ids:
            if sumo_id in self.vehicle_recorders:
                continue  # already attached
            carla_id = mapping[sumo_id]
            actor = self.world.get_actor(carla_id)
            if actor is None:
                logging.warning('[Recording] CARLA actor %s for SUMO %s not found', carla_id, sumo_id)
                continue
            
            try:
                sensor_cfgs = (
                    self._ego_vehicle_sensor_configs
                    if str(sumo_id) == str(self.ego_sumo_id)
                    else self._other_vehicle_sensor_configs
                )
                if not sensor_cfgs:
                    continue

                mgr = EgoSensorManager()
                mgr.ego_actor = actor
                mgr.ego_sumo_id = sumo_id
                mgr.ego_carla_id = carla_id
                mgr.spawn_sensors(sensor_cfgs, self.world, actor)
                
                rec = EgoRecorder()
                # Start recorder immediately if data_dump is already on
                if self.data_dump:
                    rec.start(sumo_id, save_time, sensor_cfgs)
                
                self.vehicle_recorders[sumo_id] = (mgr, rec)
                role = 'ego' if str(sumo_id) == str(self.ego_sumo_id) else 'other'
                print(f'[Recording] Attached {len(sensor_cfgs)} sensors to {role} vehicle {sumo_id} (CARLA {carla_id})')
            except Exception as e:
                logging.warning('[Recording] Failed to attach sensors to %s: %s', sumo_id, e)
        
        self.vehicles_attached = True
        print(f'[Recording] Total vehicles with sensors: {len(self.vehicle_recorders)}')

    # ------------------------------------------------------------------
    # Runtime recording toggle (called from WS command)
    # ------------------------------------------------------------------
    def _toggle_recording(self, enabled):
        """
        Start or stop recording across all instrumented vehicles at runtime.
        No process restart needed.
        """
        self.data_dump = enabled
        
        if enabled:
            if self.active_recording_save_time is None:
                self.active_recording_save_time = time.strftime('%Y_%m_%d_%H_%M_%S')
            save_time = self.active_recording_save_time
            # Attach sensors to any vehicles not yet instrumented
            if self.use_cosim and self.apply_to_all_cavs:
                self._attach_sensors_to_vehicles()
            # Start all recorders
            for sumo_id, (mgr, rec) in list(self.vehicle_recorders.items()):
                if not rec.active:
                    sensor_cfgs = (
                        self._ego_vehicle_sensor_configs
                        if str(sumo_id) == str(self.ego_sumo_id)
                        else self._other_vehicle_sensor_configs
                    )
                    rec.start(sumo_id, save_time, sensor_cfgs)
            # Runtime recording should start producing files immediately.
            # Sensor warmup has already happened while the simulation was running.
            self.record_tick_count = 60
            print(f'[Recording] Started recording for {len(self.vehicle_recorders)} vehicles')
        else:
            # Stop all recorders
            for sumo_id, (mgr, rec) in list(self.vehicle_recorders.items()):
                if rec.active:
                    rec.stop()
            self.active_recording_save_time = None
            print('[Recording] Stopped recording for all vehicles')

    # ------------------------------------------------------------------
    # Adversarial attack integration
    # ------------------------------------------------------------------
    def _ensure_attack_server_ready(self):
        """Retry model server discovery so PM2 startup ordering is not fatal."""
        if not self.attack_orchestrator:
            return False
        if self.attack_orchestrator.is_enabled():
            return True

        now = time.time()
        if now - self._last_attack_server_retry_time < 10.0:
            return False

        self._last_attack_server_retry_time = now
        try:
            return self.attack_orchestrator.refresh_server_status()
        except Exception as exc:
            logging.warning('[Adversarial] Model server check failed: %s', exc)
            return False

    @staticmethod
    def _json_safe(value):
        """Convert numpy-heavy attack info into JSON-serialisable values."""
        if isinstance(value, dict):
            return {str(k): Orchestrator._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [Orchestrator._json_safe(v) for v in value]
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        return value

    @staticmethod
    def _normalise_bgr_for_attack(image):
        """Convert recorder BGR image data into RGB float32 in [0, 1]."""
        img = np.asarray(image)
        if img.ndim != 3 or img.shape[2] < 3:
            raise ValueError(f'Expected HxWx3 camera image, got shape={img.shape}')

        rgb = img[:, :, :3][:, :, ::-1].astype(np.float32)
        if np.issubdtype(img.dtype, np.integer):
            rgb /= 255.0
        else:
            rgb = np.clip(rgb, 0.0, 1.0)
        return rgb

    @staticmethod
    def _attack_rgb_to_bgr_uint8(image):
        """Convert RGB float32 attack output back to recorder BGR uint8."""
        rgb = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
        return (rgb[:, :, ::-1] * 255.0).round().astype(np.uint8)

    @staticmethod
    def _coerce_lidar_points(points):
        """Return a float32 point cloud with at least x, y, z, intensity columns."""
        pc = np.asarray(points, dtype=np.float32)
        if pc.ndim != 2 or pc.shape[1] < 3:
            raise ValueError(f'Expected Nx3/Nx4 point cloud, got shape={pc.shape}')
        if pc.shape[1] == 3:
            pc = np.column_stack([pc, np.zeros((pc.shape[0], 1), dtype=np.float32)])
        return pc

    def _write_adversarial_metadata(self, out_dir, count, sensor_id, sensor_type, infos, clean_shape, adv_shape):
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f'{count:06d}_{sensor_id}.json')
        payload = {
            'frame': int(count),
            'timestamp': time.time(),
            'sensor_id': sensor_id,
            'sensor_type': sensor_type,
            'clean_shape': list(clean_shape),
            'adversarial_shape': list(adv_shape),
            'attacks': self._json_safe(infos),
        }
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, indent=2)

    def _write_adversarial_frame(self, rec, sensor_id, sensor_type, data, count, infos, clean_shape):
        """Persist an adversarial frame under recorder_base/<output_subdir>/."""
        if not rec or not rec.base_dir or not self.attack_orchestrator:
            return

        subdir = self.attack_orchestrator.cfg.output_subdir
        out_root = os.path.join(rec.base_dir, subdir)
        meta_dir = os.path.join(out_root, 'metadata')

        if data.get('image') is not None:
            import cv2
            images_dir = os.path.join(out_root, 'images')
            os.makedirs(images_dir, exist_ok=True)
            fname = f'{count:06d}_{sensor_id}.png'
            cv2.imwrite(os.path.join(images_dir, fname), data['image'])
            self._write_adversarial_metadata(
                meta_dir, count, sensor_id, sensor_type, infos,
                clean_shape, np.asarray(data['image']).shape,
            )
            return

        if data.get('pointcloud') is not None:
            lidar_dir = os.path.join(out_root, 'lidar')
            os.makedirs(lidar_dir, exist_ok=True)
            pc = np.asarray(data['pointcloud'], dtype=np.float32)
            try:
                import open3d as o3d
                pcd = o3d.geometry.PointCloud()
                pcd.points = o3d.utility.Vector3dVector(pc[:, :3])
                if pc.shape[1] >= 4:
                    intensity = pc[:, 3:]
                    colors = np.column_stack([
                        intensity,
                        np.zeros_like(intensity),
                        np.zeros_like(intensity),
                    ])
                    pcd.colors = o3d.utility.Vector3dVector(colors)
                fname = f'{count:06d}_{sensor_id}.pcd'
                o3d.io.write_point_cloud(os.path.join(lidar_dir, fname), pcd, write_ascii=True)
            except ImportError:
                fname = f'{count:06d}_{sensor_id}.npy'
                np.save(os.path.join(lidar_dir, fname), pc)

            self._write_adversarial_metadata(
                meta_dir, count, sensor_id, sensor_type, infos,
                clean_shape, pc.shape,
            )

    @staticmethod
    def _attack_infos_failed(infos):
        return any(info.get('error') or info.get('skipped') for info in infos or [])

    @staticmethod
    def _find_sensor_entry(sensor_data, configured_id, modality):
        """Find configured sensor id or fall back to first matching modality."""
        if configured_id in sensor_data:
            return configured_id, sensor_data[configured_id]

        for sensor_id, entry in sensor_data.items():
            sensor_type = entry.get('type', '')
            if modality == 'lidar' and 'lidar' in sensor_type and entry.get('pointcloud') is not None:
                return sensor_id, entry
            if modality == 'camera' and 'camera' in sensor_type and entry.get('image') is not None:
                return sensor_id, entry

        return configured_id, None

    @staticmethod
    def _normalise_angle_rad(angle):
        return (angle + math.pi) % (2.0 * math.pi) - math.pi

    def _build_lidar_gt_boxes(self, mgr, lidar_sensor_id=None, points=None):
        """Build OpenPCDet-style GT boxes [x,y,z,dx,dy,dz,yaw] in LiDAR coordinates."""
        ego_actor = getattr(mgr, 'ego_actor', None)
        if ego_actor is None or not ego_actor.is_alive:
            return None

        lidar_slot = None
        for slot in getattr(mgr, '_slots', []):
            if 'lidar' not in getattr(slot, 'sensor_type', ''):
                continue
            if lidar_sensor_id is None or slot.sensor_id == lidar_sensor_id:
                lidar_slot = slot
                break
            if lidar_slot is None:
                lidar_slot = slot

        if lidar_slot is None or lidar_slot.carla_sensor is None:
            return None

        categories = self.gt_detection_categories or ['vehicle']
        actor_filters = []
        if 'vehicle' in categories:
            actor_filters.append('vehicle.*')
        if 'walker' in categories:
            actor_filters.append('walker.*')

        world = ego_actor.get_world()
        lidar_transform = lidar_slot.carla_sensor.get_transform()
        lidar_inv = np.asarray(lidar_transform.get_inverse_matrix(), dtype=np.float32)
        lidar_yaw = math.radians(float(lidar_transform.rotation.yaw))

        max_range = None
        if points is not None and len(points) > 0:
            max_range = float(np.linalg.norm(np.asarray(points)[:, :3], axis=1).max()) + 5.0

        boxes = []
        seen_actor_ids = set()
        for actor_filter in actor_filters:
            for actor in world.get_actors().filter(actor_filter):
                if actor.id == ego_actor.id or actor.id in seen_actor_ids:
                    continue
                seen_actor_ids.add(actor.id)

                try:
                    actor_transform = actor.get_transform()
                    bbox = actor.bounding_box
                    center_world = actor_transform.transform(bbox.location)
                    center = lidar_inv @ np.array(
                        [center_world.x, center_world.y, center_world.z, 1.0],
                        dtype=np.float32,
                    )
                    x, y, z = [float(v) for v in center[:3]]
                    if max_range is not None and math.sqrt(x * x + y * y + z * z) > max_range:
                        continue

                    extent = bbox.extent
                    dx = float(extent.x) * 2.0
                    dy = float(extent.y) * 2.0
                    dz = float(extent.z) * 2.0
                    yaw = self._normalise_angle_rad(
                        math.radians(float(actor_transform.rotation.yaw)) - lidar_yaw
                    )
                    boxes.append([x, y, z, dx, dy, dz, yaw])
                except Exception as exc:
                    logging.debug('[GT] Failed to build lidar GT box for actor=%s: %s', actor.id, exc)

        if not boxes:
            return None
        return np.asarray(boxes, dtype=np.float32)

    def _apply_adversarial_attacks(self, sensor_data, mgr, rec, count, vehicle_id=None):
        """Run configured attacks for a recorded vehicle's available sensors."""
        if not self._ensure_attack_server_ready():
            return sensor_data

        attack_orch = self.attack_orchestrator
        cfg = attack_orch.cfg
        updated = dict(sensor_data)

        lidar_id, lidar_entry = self._find_sensor_entry(sensor_data, cfg.lidar_sensor_id, 'lidar')
        if cfg.attacks_3d and lidar_entry and lidar_entry.get('pointcloud') is not None:
            try:
                clean_pc = self._coerce_lidar_points(lidar_entry['pointcloud'])
                gt_boxes = self._build_lidar_gt_boxes(mgr, lidar_id, clean_pc)
                adv_pc, infos_3d = attack_orch.run_all_3d(clean_pc, gt_boxes=gt_boxes)
                gt_boxes_used = gt_boxes.tolist() if gt_boxes is not None else []
                for info in infos_3d or []:
                    info['gt_boxes_used'] = len(gt_boxes_used)
                    info['gt_boxes_lidar'] = gt_boxes_used
                if self._attack_infos_failed(infos_3d):
                    logging.warning('[Adversarial] 3-D attacks skipped/failed for %s', lidar_id)
                else:
                    adv_entry = dict(lidar_entry)
                    adv_entry['pointcloud'] = np.asarray(adv_pc, dtype=np.float32)
                    if cfg.save_adversarial:
                        self._write_adversarial_frame(
                            rec, lidar_id, lidar_entry['type'], adv_entry, count,
                            infos_3d, clean_pc.shape,
                        )
                    if not cfg.save_clean:
                        updated[lidar_id] = adv_entry
                    logging.info(
                        '[Adversarial] vehicle=%s frame=%s lidar=%s attacks=%d gt_boxes=%d clean_points=%d adv_points=%d',
                        vehicle_id, count, lidar_id, len(infos_3d),
                        len(gt_boxes_used),
                        clean_pc.shape[0], adv_entry['pointcloud'].shape[0],
                    )
            except Exception as exc:
                logging.exception('[Adversarial] 3-D attack failed for %s: %s', lidar_id, exc)

        camera_id, camera_entry = self._find_sensor_entry(sensor_data, cfg.camera_sensor_id, 'camera')
        if cfg.attacks_2d and camera_entry and camera_entry.get('image') is not None:
            try:
                clean_rgb = self._normalise_bgr_for_attack(camera_entry['image'])
                adv_rgb, infos_2d = attack_orch.run_all_2d(clean_rgb)
                if self._attack_infos_failed(infos_2d):
                    logging.warning('[Adversarial] 2-D attacks skipped/failed for %s', camera_id)
                else:
                    adv_entry = dict(camera_entry)
                    adv_entry['image'] = self._attack_rgb_to_bgr_uint8(adv_rgb)
                    if cfg.save_adversarial:
                        self._write_adversarial_frame(
                            rec, camera_id, camera_entry['type'], adv_entry, count,
                            infos_2d, np.asarray(camera_entry['image']).shape,
                        )
                    if not cfg.save_clean:
                        updated[camera_id] = adv_entry
                    logging.info(
                        '[Adversarial] vehicle=%s frame=%s camera=%s attacks=%d',
                        vehicle_id, count, camera_id, len(infos_2d),
                    )
            except Exception as exc:
                logging.exception('[Adversarial] 2-D attack failed for %s: %s', camera_id, exc)

        return updated

    def start_websocket_server(self):
        """Start WebSocket server for frontend communication."""
        def run_server():
            self.ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.ws_loop)
            
            async def handler(websocket, path):
                self.ws_clients.add(websocket)
                print(f"[WS] Client connected. Total: {len(self.ws_clients)}")
                try:
                    async for message in websocket:
                        try:
                            msg = json.loads(message)
                            msg_type = msg.get('type', '')
                            if msg_type == 'toggle_recording':
                                enabled = bool(msg.get('enabled', False))
                                print(f'[WS] Received toggle_recording: enabled={enabled}')
                                self._toggle_recording(enabled)
                                await websocket.send(json.dumps({
                                    'type': 'recording_status',
                                    'recording': enabled,
                                    'vehicles': len(self.vehicle_recorders)
                                }))
                            else:
                                logging.debug('[WS] Unknown message type: %s', msg_type)
                        except json.JSONDecodeError:
                            logging.warning('[WS] Invalid JSON message received')
                except websockets.exceptions.ConnectionClosed:
                    pass
                finally:
                    self.ws_clients.discard(websocket)
                    print(f"[WS] Client disconnected. Total: {len(self.ws_clients)}")
            
            async def start_server():
                self.ws_server = await websockets.serve(handler, "0.0.0.0", self.ws_port)
                await self.ws_server.wait_closed()
            
            self.ws_loop.run_until_complete(start_server())
        
        self.ws_thread = threading.Thread(target=run_server, daemon=True)
        self.ws_thread.start()
    
    def broadcast_vehicle_positions(self, vehicles_data):
        """Broadcast vehicle positions to WebSocket clients."""
        if not self.ws_clients:
            return
        
        message = json.dumps({
            'type': 'vehicle_positions',
            'timestamp': time.time(),
            'vehicles': vehicles_data
        })
        
        for client in self.ws_clients.copy():
            try:
                if self.ws_loop:
                    asyncio.run_coroutine_threadsafe(client.send(message), self.ws_loop)
            except Exception as e:
                print(f"[WS] Error sending to client: {e}")
    
    def collect_vehicle_positions(self):
        """Collect vehicle positions from local vehicle managers and CARLA world."""
        vehicles_data = []
        
        # Collect from local vehicle managers.
        for vm in self.vehicle_managers:
            vehicle = vm.vehicle
            transform = vehicle.get_transform()
            vel = vehicle.get_velocity()
            speed = float((vel.x ** 2 + vel.y ** 2 + vel.z ** 2) ** 0.5)
            heading = float(transform.rotation.yaw)
            loc = transform.location
            
            vehicles_data.append({
                'id': str(vehicle.id),
                'carla_x': float(loc.x),
                'carla_y': float(loc.y),
                'carla_z': float(loc.z),
                'gps_lon': None,
                'gps_lat': None,
                'speed': speed,
                'heading': heading,
                'carla_id': vehicle.id,
                'role_name': vehicle.attributes.get('role_name')
            })
            
            # Track position
            self.position_tracker.track_position(
                vehicle_id=str(vehicle.id),
                carla_x=loc.x,
                carla_y=loc.y,
                carla_z=loc.z,
                speed=speed,
                heading=heading
            )
        
        # Also collect from SUMO-controlled vehicles (if co-simulation)
        if self.use_cosim:
            # Get vehicles from SUMO that are not CAVs
            for sumo_id, carla_id in self.scenario_manager.sumo2carla_ids.items():
                if carla_id not in [vm.vehicle.id for vm in self.vehicle_managers]:
                    try:
                        vehicle = self.world.get_actor(carla_id)
                        if vehicle:
                            transform = vehicle.get_transform()
                            vel = vehicle.get_velocity()
                            speed = float((vel.x ** 2 + vel.y ** 2 + vel.z ** 2) ** 0.5)
                            heading = float(transform.rotation.yaw)
                            loc = transform.location
                            
                            vehicles_data.append({
                                'id': str(sumo_id),
                                'carla_x': float(loc.x),
                                'carla_y': float(loc.y),
                                'carla_z': float(loc.z),
                                'speed': speed,
                                'heading': heading,
                                'carla_id': carla_id,
                                'sumo_id': sumo_id
                            })
                    except Exception as e:
                        pass  # Vehicle may have been destroyed
        
        return vehicles_data
    
    def step(self):
        """Execute a single simulation step with Artery integration."""

        # Step the local scenario manager, which handles CARLA-SUMO sync.
        if self.use_cosim:
            # CoScenarioManager.tick() handles both CARLA and SUMO
            self.scenario_manager.tick()
        else:
            # CARLA-only: just tick the world
            self.world.tick()
            # Tick traffic manager if it exists
            if hasattr(self.scenario_manager, 'traffic_manager'):
                self.scenario_manager.tick()
        
        # Process Artery CAM messages if connected
        self._current_step_cams = []
        if self.artery_conn:
            self.artery_conn.checkAndConnectclient()
        if self.artery_conn and self.artery_conn.is_connected():
            # Use wrapper that provides net access for Artery
            sync_obj = self.artery_sync_wrapper if self.artery_sync_wrapper else self.scenario_manager
            self._current_step_cams = self.artery_conn.recieve_cam_messages(sync_obj)
            
            # Process CAM messages (could be used for V2X, attacks, etc.)
            for cam in self._current_step_cams:
                vehicle_id = cam.get('receiver_sumo_id')
                if vehicle_id:
                    # Track position from CAM
                    carla_x = cam.get('receiver_pos_x', 0)
                    carla_y = cam.get('receiver_pos_y', 0)
                    gps_lon = cam.get('receiver_long')
                    gps_lat = cam.get('receiver_lat')
                    speed = cam.get('receiver_speed')
                    heading = cam.get('Heading')
                    
                    self.position_tracker.track_position(
                        vehicle_id=vehicle_id,
                        carla_x=carla_x,
                        carla_y=carla_y,
                        gps_lon=gps_lon,
                        gps_lat=gps_lat,
                        speed=speed,
                        heading=heading
                    )
            
            self.cams.extend(self._current_step_cams)
        
        # Update vehicle managers
        for vm in self.vehicle_managers:
            vm.update_info()
            control = vm.run_step()
            vm.vehicle.apply_control(control)
        
        # --- Multi-vehicle sensor attachment (runs each tick to catch late-spawning vehicles) ---
        if self.use_cosim and (self._other_vehicle_sensor_configs or self._ego_vehicle_sensor_configs) and self.apply_to_all_cavs:
            try:
                mapping = self.scenario_manager.sumo2carla_ids
                cap = self.max_record_vehicles or 0
                if mapping and (cap == 0 or len(self.vehicle_recorders) < cap):
                    self._attach_sensors_to_vehicles()
            except Exception as e:
                logging.warning('[Orchestrator] Vehicle sensor attach failed: %s', e)
        
        # --- Multi-vehicle sensor data collection & recording ---
        if self.vehicle_recorders:
            self.record_tick_count += 1
            
            for sumo_id, (mgr, rec) in list(self.vehicle_recorders.items()):
                telemetry = mgr.get_ego_telemetry()
                
                # Record to disk if data_dump enabled and recorder is active
                if self.data_dump and rec.active:
                    if rec.should_record(self.record_tick_count):
                        sensor_data = mgr.get_latest_data()
                        # --- Apply adversarial attacks to every recorded vehicle ---
                        if self.attack_orchestrator:
                            sensor_data = self._apply_adversarial_attacks(
                                sensor_data, mgr, rec, self.record_tick_count, vehicle_id=sumo_id)
                        for sid, sdata in sensor_data.items():
                            rec.record_frame(
                                sid, sdata['type'], sdata, self.record_tick_count)
                        rec.record_telemetry(
                            telemetry, self.record_tick_count)
                        if self.gt_detections_enabled:
                            gt_payload = mgr.build_ground_truth(self.gt_detection_categories)
                            if gt_payload:
                                gt_payload.update({
                                    'frame': self.record_tick_count,
                                    'timestamp': time.time(),
                                })
                                gt_stats = gt_payload.get('debug_stats', {})
                                per_sensor_stats = gt_stats.get('per_sensor', {})
                                sensor_summaries = []
                                for sensor_id, stats in per_sensor_stats.items():
                                    sensor_summaries.append(
                                        f"{sensor_id}: kept={stats.get('annotations_kept', 0)}/"
                                        f"{stats.get('actors_considered', 0)}, "
                                        f"seg_miss={stats.get('segmentation_no_matching_pixels', 0)}, "
                                        f"behind={stats.get('behind_camera', 0)}, "
                                        f"proj_err={stats.get('projection_errors', 0)}"
                                    )
                                print(
                                    f"[GT] vehicle={sumo_id} frame={self.record_tick_count} "
                                    f"total_kept={gt_stats.get('annotations_total', 0)} "
                                    f"actors_total={gt_stats.get('actors_total', 0)} "
                                    + " | ".join(sensor_summaries)
                                )
                                rec.record_ground_truth(gt_payload, self.record_tick_count)
                                if self.gt_detections_debug_draw:
                                    rec.record_ground_truth_debug(
                                        gt_payload, sensor_data, self.record_tick_count
                                    )
                        # Record V2X CAM messages addressed to this vehicle
                        vehicle_cams = [
                            c for c in self._current_step_cams
                            if str(c.get('receiver_sumo_id')) == str(sumo_id)
                        ]
                        if vehicle_cams:
                            rec.record_v2x(vehicle_cams, self.record_tick_count)
            
        # Broadcast ego/preview data over WebSocket for UI preview.
        # Priority: instrumented recorder > vehicle_managers > raw SUMO-CARLA mapping.
        # Always broadcast (even without telemetry) so the UI gets frame/sim_seconds.
        if self.ws_clients:
            telemetry = None
            camera = None
            recording_active = False

            first_sumo_id = (self.ego_sumo_id if self.ego_sumo_id in self.vehicle_recorders
                             else next(iter(self.vehicle_recorders), None))
            if first_sumo_id is not None:
                first_mgr, first_rec = self.vehicle_recorders[first_sumo_id]
                telemetry = first_mgr.get_ego_telemetry()
                cam_sid, cam_b64 = first_mgr.get_camera_jpeg_base64(quality=50)
                if cam_b64:
                    camera = {'sensor_id': cam_sid, 'jpeg_base64': cam_b64}
                recording_active = self.data_dump and first_rec.active
            elif self.vehicle_managers:
                vehicle = self.vehicle_managers[0].vehicle
                if vehicle and vehicle.is_alive:
                    transform = vehicle.get_transform()
                    velocity = vehicle.get_velocity()
                    accel = vehicle.get_acceleration()
                    speed_ms = (velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2) ** 0.5
                    telemetry = {
                        'carla_id': vehicle.id,
                        'sumo_id': None,
                        'x': float(transform.location.x),
                        'y': float(transform.location.y),
                        'z': float(transform.location.z),
                        'roll': float(transform.rotation.roll),
                        'pitch': float(transform.rotation.pitch),
                        'yaw': float(transform.rotation.yaw),
                        'speed_ms': float(speed_ms),
                        'speed_kmh': float(speed_ms * 3.6),
                        'velocity': {'x': float(velocity.x), 'y': float(velocity.y), 'z': float(velocity.z)},
                        'acceleration': {'x': float(accel.x), 'y': float(accel.y), 'z': float(accel.z)},
                    }
            elif self.use_cosim:
                # Fallback: any live CARLA actor from the SUMO co-sim mapping
                try:
                    mapping = self.scenario_manager.sumo2carla_ids
                    ego_sid = self._resolve_ego_sumo_id(mapping) if mapping else None
                    cid = (mapping.get(ego_sid) if ego_sid
                           else (next(iter(mapping.values()), None) if mapping else None))
                    if cid:
                        actor = self.world.get_actor(cid)
                        if actor and actor.is_alive:
                            transform = actor.get_transform()
                            velocity = actor.get_velocity()
                            accel = actor.get_acceleration()
                            speed_ms = (velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2) ** 0.5
                            telemetry = {
                                'carla_id': actor.id,
                                'sumo_id': ego_sid,
                                'x': float(transform.location.x),
                                'y': float(transform.location.y),
                                'z': float(transform.location.z),
                                'roll': float(transform.rotation.roll),
                                'pitch': float(transform.rotation.pitch),
                                'yaw': float(transform.rotation.yaw),
                                'speed_ms': float(speed_ms),
                                'speed_kmh': float(speed_ms * 3.6),
                                'velocity': {'x': float(velocity.x), 'y': float(velocity.y), 'z': float(velocity.z)},
                                'acceleration': {'x': float(accel.x), 'y': float(accel.y), 'z': float(accel.z)},
                            }
                except Exception:
                    pass

            # Always send — even without telemetry — so frame/sim_seconds reach the UI
            ego_msg = json.dumps({
                'type': 'ego_sensor_data',
                'timestamp': time.time(),
                'frame': self.record_tick_count,
                'sim_seconds': round(time.time() - self.sim_start_time, 3),
                'status': {
                    'state': 'running',
                    'phase': 'recording' if recording_active else 'streaming',
                    'recording': recording_active,
                    'recording_vehicles': len(self.vehicle_recorders),
                    'attacks_enabled': bool(self.attack_orchestrator and self.attack_orchestrator.is_enabled()),
                },
                'telemetry': telemetry,
                'camera': camera,
                'recording': recording_active,
                'recording_vehicles': len(self.vehicle_recorders)
            })
            for client in self.ws_clients.copy():
                try:
                    if self.ws_loop:
                        asyncio.run_coroutine_threadsafe(client.send(ego_msg), self.ws_loop)
                except Exception:
                    pass
        
        # Collect and broadcast vehicle positions
        vehicles_data = self.collect_vehicle_positions()
        self.broadcast_vehicle_positions(vehicles_data)

    
    def loop(self):
        """Main simulation loop."""
        try:
            print("[Orchestrator] Starting simulation loop...")
            while True:
                self.step()
        except KeyboardInterrupt:
            logging.info('[Orchestrator] Cancelled by user.')
        finally:
            self.close()
    
    def close(self):
        """Cleanup and close all connections."""
        print("[Orchestrator] Cleaning up...")
        
        # Stop all vehicle recorders and destroy sensors
        for sumo_id, (mgr, rec) in list(self.vehicle_recorders.items()):
            try:
                if rec.active:
                    rec.stop()
                mgr.destroy()
            except Exception as e:
                logging.warning('[Orchestrator] Error cleaning up recorder for %s: %s', sumo_id, e)
        self.vehicle_recorders.clear()
        
        # Shutdown thread pool
        if hasattr(self, 'step_executor') and self.step_executor:
            try:
                self.step_executor.shutdown(wait=False)
            except Exception:
                pass
        
        # Save position tracking
        try:
            self.position_tracker.print_summary()
            json_file = self.position_tracker.save_to_file()
            print(f"[Orchestrator] Position data saved to {json_file}")
        except Exception as e:
            print(f"[Orchestrator] Error saving position data: {e}")
        
        if self.eval_manager:
            try:
                self.eval_manager.evaluate()
            except Exception as e:
                print(f"[Orchestrator] Error in evaluation: {e}")
        
        # Destroy vehicle managers
        for vm in self.vehicle_managers:
            try:
                vm.destroy()
            except Exception as e:
                print(f"[Orchestrator] Error destroying vehicle manager: {e}")
        
        # Close scenario manager
        try:
            self.scenario_manager.close()
        except Exception as e:
            print(f"[Orchestrator] Error closing scenario manager: {e}")
        
        # Close Artery connection
        if self.artery_conn:
            try:
                self.artery_conn.shutdownAndClose()
            except Exception as e:
                print(f"[Orchestrator] Error closing Artery connection: {e}")
        
        print("[Orchestrator] Cleanup complete")


def main():
    """Main entry point."""
    argparser = argparse.ArgumentParser(description='Simulator orchestrator with Artery integration')
    
    # Configuration files
    argparser.add_argument('--scenario-config', type=str, required=True,
                          help='Path to scenario YAML configuration file')
    argparser.add_argument('--general-config', type=str, default=None,
                          help='Path to general configuration YAML file (merged with scenario config). '
                               'If not provided, looks for general_config.yaml in the same directory as scenario config.')
    
    # CARLA settings
    argparser.add_argument('--carla-town', type=str, default='Town01',
                          help='CARLA town name (default: Town01)')
    argparser.add_argument('--carla-host', type=str, default='127.0.0.1',
                          help='CARLA host (default: 127.0.0.1)')
    argparser.add_argument('--carla-port', type=int, default=2000,
                          help='CARLA port (default: 2000)')
    argparser.add_argument('--carla-version', type=str, default='0.9.15',
                          help='CARLA version (default: 0.9.15)')
    argparser.add_argument('--xodr-path', type=str, default=None,
                          help='Path to custom XODR map file')
    
    # SUMO settings (for co-simulation)
    argparser.add_argument('--sumo-cfg-file', type=str, default=None,
                          help='Path to SUMO configuration file')
    argparser.add_argument('--sumo-file-parent-path', type=str, default=None,
                          help='Parent path containing SUMO .sumocfg, .net.xml, .rou.xml files')
    
    # Artery settings
    argparser.add_argument('--start-artery', action='store_true',
                          help='Enable Artery synchronization')
    argparser.add_argument('--artery-control', action='store_true', default=True,
                          help='Enable Artery ExternalControl bridge')
    argparser.add_argument('--artery-host', type=str, default='127.0.0.1',
                          help='Artery ExternalControl host')
    argparser.add_argument('--artery-port', type=int, default=8888,
                          help='Artery ExternalControl port')
    
    # Simulator module settings
    argparser.add_argument('--apply-ml', action='store_true',
                          help='Apply machine learning models')
    argparser.add_argument('--data-dump', action='store_true',
                          help='Enable data dumping (sensors + ground truth)')
    
    # Optional features
    argparser.add_argument('--enable-ws', action='store_true', default=True,
                          help='Enable WebSocket server for frontend (default: True)')
    argparser.add_argument('--ws-port', type=int, default=8765,
                          help='WebSocket server port')
    argparser.add_argument('--enable-map', action='store_true',
                          help='Enable real-time map visualization')
    argparser.add_argument('--map-use-gps', action='store_true',
                          help='Use GPS coordinates for map')
    argparser.add_argument('--map-update-interval', type=float, default=1.0,
                          help='Map update interval in seconds')
    argparser.add_argument('--track-vehicles', type=str, default=None,
                          help='Comma-separated list of vehicle IDs to track')
    argparser.add_argument('--enable-evaluation', action='store_true',
                          help='Enable evaluation manager')
    argparser.add_argument('--debug', action='store_true',
                          help='Enable debug logging')
    
    arguments = argparser.parse_args()
    
    # Setup logging
    if arguments.debug:
        logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.DEBUG)
    else:
        logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)
    
    # Create and run orchestrator
    orchestrator = Orchestrator(arguments)
    orchestrator.loop()


if __name__ == '__main__':
    main()
