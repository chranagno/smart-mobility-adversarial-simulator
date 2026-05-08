#!/usr/bin/env python3
"""
Config Launcher Service
A persistent service that exposes an API to configure Carla with different scenarios.
"""

import os
import sys
import time
import socket
import threading
import subprocess
from flask import Flask, request, jsonify
from flask_cors import CORS

# Add Carla Python API to path
carla_home = os.environ.get('CARLA_HOME', '')
if carla_home:
    sys.path.insert(0, carla_home)
    # Try to add carla egg
    import glob
    egg_paths = glob.glob(os.path.join(carla_home, 'carla/dist/carla-*%d.%d-%s.egg' % (
        sys.version_info.major,
        sys.version_info.minor,
        'win-amd64' if os.name == 'nt' else 'linux-x86_64')))
    if egg_paths:
        sys.path.insert(0, egg_paths[0])

try:
    import carla
except ImportError:
    print("Warning: Could not import carla module. Make sure CARLA_HOME is set correctly.")
    carla = None

app = Flask(__name__)
CORS(app)

# Global state
current_status = {
    'scenario_name': None,
    'status': 'idle',  # idle, loading, loaded, error
    'message': 'Service ready',
    'town': None,
    'carla_host': None,
    'carla_port': None
}

# Lock for thread-safe operations
status_lock = threading.Lock()

# Default configuration from environment
DEFAULT_CARLA_HOST = os.environ.get('CARLA_HOST', '127.0.0.1')
DEFAULT_CARLA_PORT = int(os.environ.get('CARLA_PORT', '2000'))
SRC_DIR = os.environ.get('SRC_DIR', os.path.dirname(os.path.abspath(__file__)))
MAX_WAIT = int(os.environ.get('MAX_WAIT', '120'))


def wait_for_carla(host, port, max_wait):
    """Wait for Carla to be ready on the specified port."""
    waited = 0
    while waited < max_wait:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                return True
        except Exception:
            pass
        time.sleep(1)
        waited += 1
        if waited % 10 == 0:
            print(f"   Still waiting for Carla... ({waited}s elapsed)")
    return False


def reload_carla_world(client, max_retries=3):
    """Reload the Carla world for a clean state."""
    for attempt in range(max_retries):
        try:
            # Increase timeout for reload operation
            client.set_timeout(30.0)
            world = client.get_world()
            current_map = world.get_map().name
            print(f"   Current map: {current_map}")
            print(f"   Reloading world... (attempt {attempt + 1}/{max_retries})")
            
            # Try to reload the world
            client.reload_world(False)  # False = don't reset settings
            
            # Wait for reload to complete
            time.sleep(3)  # Increased wait time
            
            # Verify the world is accessible after reload
            world = client.get_world()
            world.get_map()  # Test if map is accessible
            
            print("✅ World reloaded successfully")
            return True
        except Exception as e:
            error_msg = str(e)
            print(f"❌ Error reloading world (attempt {attempt + 1}/{max_retries}): {error_msg}")
            if attempt < max_retries - 1:
                # Wait before retrying
                wait_time = (attempt + 1) * 2  # Exponential backoff: 2s, 4s, 6s
                print(f"   Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                # Last attempt failed
                print(f"❌ Failed to reload world after {max_retries} attempts")
                return False
    return False


def load_map(client, town):
    """Load a map into Carla using config.py."""
    try:
        config_script = os.path.join(SRC_DIR, 'config.py')
        if not os.path.exists(config_script):
            raise FileNotFoundError(f"config.py not found at {config_script}")
        
        # Run config.py to load the map
        result = subprocess.run(
            [sys.executable, config_script, '-m', town, 
             '--host', DEFAULT_CARLA_HOST, '--port', str(DEFAULT_CARLA_PORT)],
            cwd=SRC_DIR,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"config.py failed: {result.stderr}")
        
        print(f"✅ Map '{town}' loaded successfully")
        return True
    except Exception as e:
        print(f"❌ Error loading map: {e}")
        return False


def configure_carla(scenario_name, town, carla_host, carla_port):
    """Configure Carla with the given parameters."""
    global current_status
    
    with status_lock:
        current_status['status'] = 'loading'
        current_status['scenario_name'] = scenario_name
        current_status['town'] = town
        current_status['carla_host'] = carla_host
        current_status['carla_port'] = carla_port
        current_status['message'] = 'Starting configuration...'
    
    try:
        print(f"\n{'='*50}")
        print(f"Configuring Carla for scenario: {scenario_name}")
        print(f"Town: {town}")
        print(f"Host: {carla_host}:{carla_port}")
        print(f"{'='*50}\n")
        
        # Step 1: Wait for Carla
        print("⏳ Waiting for Carla to be ready...")
        with status_lock:
            current_status['message'] = 'Waiting for Carla...'
        
        if not wait_for_carla(carla_host, carla_port, MAX_WAIT):
            raise RuntimeError(f"Carla not available on {carla_host}:{carla_port} after {MAX_WAIT}s")
        
        print("✅ Carla is ready")
        
        # Step 2: Wait for full initialization
        print("⏳ Waiting for Carla to fully initialize...")
        time.sleep(10)  # Increased wait time to ensure Carla is fully ready
        
        # Step 3: Connect and reload world
        if carla is None:
            raise RuntimeError("Carla module not available")
        
        print("🔄 Connecting to Carla and reloading world...")
        with status_lock:
            current_status['message'] = 'Reloading Carla world...'
        
        # Connect with longer timeout
        client = carla.Client(carla_host, carla_port)
        client.set_timeout(20.0)  # Increased timeout for initial connection
        
        # Test connection by getting world
        try:
            world = client.get_world()
            print("✅ Connected to Carla successfully")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Carla: {e}")
        
        if not reload_carla_world(client):
            raise RuntimeError("Failed to reload Carla world")
        
        # Step 4: Load map
        print(f"🚀 Loading map: {town}")
        with status_lock:
            current_status['message'] = f'Loading map {town}...'
        
        if not load_map(client, town):
            raise RuntimeError(f"Failed to load map {town}")
        
        # Success!
        with status_lock:
            current_status['status'] = 'loaded'
            current_status['message'] = f'Scenario {scenario_name} loaded successfully'
        
        print(f"\n✅ Configuration complete for scenario: {scenario_name}")
        return True
        
    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ Configuration failed: {error_msg}")
        with status_lock:
            current_status['status'] = 'error'
            current_status['message'] = f'Error: {error_msg}'
        return False


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'service': 'config-launcher'
    })


@app.route('/status', methods=['GET'])
def get_status():
    """Get current configuration status."""
    with status_lock:
        status = current_status.copy()
    
    # Format response as requested: scenario_name: loaded
    if status['status'] == 'loaded':
        response = {
            status['scenario_name']: 'loaded'
        }
    else:
        response = {
            'status': status['status'],
            'scenario_name': status['scenario_name'],
            'message': status['message'],
            'town': status['town']
        }
    
    return jsonify(response)


@app.route('/load', methods=['POST'])
def load_scenario():
    """Load a scenario configuration."""
    global current_status
    
    data = request.get_json() or {}
    
    # Get parameters from request body or environment variables
    scenario_name = data.get('scenario_name') or os.environ.get('SCENARIO_NAME', 'default')
    town = data.get('town') or os.environ.get('TOWN', 'Town01')
    carla_host = data.get('carla_host') or os.environ.get('CARLA_HOST', DEFAULT_CARLA_HOST)
    carla_port = data.get('carla_port') or int(os.environ.get('CARLA_PORT', DEFAULT_CARLA_PORT))
    
    # Check if already loading
    with status_lock:
        if current_status['status'] == 'loading':
            return jsonify({
                'success': False,
                'error': 'Configuration already in progress'
            }), 400
    
    # Start configuration in background thread
    def configure_thread():
        configure_carla(scenario_name, town, carla_host, carla_port)
    
    thread = threading.Thread(target=configure_thread, daemon=True)
    thread.start()
    
    return jsonify({
        'success': True,
        'message': f'Configuration started for scenario: {scenario_name}',
        'scenario_name': scenario_name,
        'town': town
    })


@app.route('/reset', methods=['POST'])
def reset():
    """Reset the service state."""
    global current_status
    with status_lock:
        current_status = {
            'scenario_name': None,
            'status': 'idle',
            'message': 'Service ready',
            'town': None,
            'carla_host': None,
            'carla_port': None
        }
    return jsonify({
        'success': True,
        'message': 'Service state reset'
    })


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Config Launcher Service')
    parser.add_argument('--host', default='127.0.0.1', help='Service host (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=5001, help='Service port (default: 5001)')
    parser.add_argument('--carla-host', default=DEFAULT_CARLA_HOST, help='Carla host')
    parser.add_argument('--carla-port', type=int, default=DEFAULT_CARLA_PORT, help='Carla port')
    parser.add_argument('--auto-connect', action='store_true', help='Auto-connect to Carla on startup')
    
    args = parser.parse_args()
    
    # Update defaults if provided
    DEFAULT_CARLA_HOST = args.carla_host
    DEFAULT_CARLA_PORT = args.carla_port
    
    print("="*50)
    print("Config Launcher Service")
    print("="*50)
    print(f"Service: http://{args.host}:{args.port}")
    print(f"Carla: {DEFAULT_CARLA_HOST}:{DEFAULT_CARLA_PORT}")
    print(f"Source Dir: {SRC_DIR}")
    print("="*50)
    print()
    
    # Auto-connect if requested
    if args.auto_connect:
        print("Auto-connect enabled, waiting for Carla...")
        if wait_for_carla(DEFAULT_CARLA_HOST, DEFAULT_CARLA_PORT, MAX_WAIT):
            print("✅ Carla is ready")
        else:
            print("⚠️  Carla not ready, but service will start anyway")
    
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
