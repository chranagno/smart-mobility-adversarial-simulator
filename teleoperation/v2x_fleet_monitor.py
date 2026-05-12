#!/usr/bin/env python

# V2X FLEET TELEOPERATOR MONITORING SYSTEM
# Multi-vehicle monitoring with 1m vs 5m localization demo

"""
V2X FLEET TELEOPERATOR MONITOR

    === MAP CONTROLS ===
    Mouse Wheel  : zoom in (only)
    Mouse Drag   : pan map (when zoomed in)
    Click        : select vehicle
    
    === VEHICLE CONTROLS ===
    W/S/A/D      : throttle/brake/steer (selected vehicle)
    P            : toggle autopilot

    === V2X DEMO CONTROLS ===
    1            : Set 1m localization
    5            : Set 5m localization
    R            : Reset correct lane
    C            : Clear alert
    T            : Spawn more traffic
    
    ESC          : quit
"""

import glob
import os
import sys

try:
    sys.path.append(glob.glob('../carla/dist/carla-*%d.%d-%s.egg' % (
        sys.version_info.major,
        sys.version_info.minor,
        'win-amd64' if os.name == 'nt' else 'linux-x86_64'))[0])
except IndexError:
    pass

import carla

import argparse
import math
import random
import hashlib
import subprocess
import time
import threading
from collections import deque

try:
    import pygame
    from pygame.locals import *
except ImportError:
    raise RuntimeError('cannot import pygame, make sure pygame package is installed')

# ==============================================================================
# -- Constants -----------------------------------------------------------------
# ==============================================================================

# Colors - Professional dark theme
COLOR_BG_DARK = pygame.Color(18, 22, 28)
COLOR_PANEL_BG = pygame.Color(28, 34, 42)
COLOR_PANEL_HEADER = pygame.Color(35, 42, 52)
COLOR_BORDER = pygame.Color(50, 60, 75)
COLOR_TEXT_PRIMARY = pygame.Color(230, 235, 245)
COLOR_TEXT_SECONDARY = pygame.Color(140, 150, 165)
COLOR_TEXT_MUTED = pygame.Color(90, 100, 115)

COLOR_STATUS_OK = pygame.Color(46, 204, 113)
COLOR_STATUS_WARNING = pygame.Color(241, 196, 15)
COLOR_STATUS_ALERT = pygame.Color(231, 76, 60)
COLOR_STATUS_UNCERTAIN = pygame.Color(155, 89, 182)
COLOR_SELECTED = pygame.Color(52, 152, 219)

COLOR_ACCENT_BLUE = pygame.Color(52, 152, 219)
COLOR_ACCENT_CYAN = pygame.Color(26, 188, 156)

# Map colors
COLOR_ROAD = pygame.Color(46, 52, 54)
COLOR_ROAD_MARKING = pygame.Color(186, 189, 182)
COLOR_VEHICLE = pygame.Color(114, 159, 207)
COLOR_VEHICLE_NPC = pygame.Color(100, 120, 140)
COLOR_WHITE = pygame.Color(255, 255, 255)
COLOR_BLACK = pygame.Color(0, 0, 0)

# Layout
PANEL_WIDTH = 450  # Wider panel for larger fonts
PIXELS_PER_METER = 12
MAP_DEFAULT_SCALE = 0.2  # Zoomed out view (smaller = more map visible)
MAP_MIN_SCALE = 0.2  # Minimum zoom (most zoomed out)
MAP_MAX_SCALE = 2.0

# ==============================================================================
# -- Utility -------------------------------------------------------------------
# ==============================================================================

def get_actor_display_name(actor, truncate=250):
    name = ' '.join(actor.type_id.replace('_', '.').title().split('.')[1:])
    return (name[:truncate - 1] + '...') if len(name) > truncate else name


# ==============================================================================
# -- Event Log -----------------------------------------------------------------
# ==============================================================================

class EventLog:
    """Maintains a log of events for display"""
    def __init__(self, max_events=50):
        self.events = deque(maxlen=max_events)
        
    def add(self, message, level="INFO"):
        timestamp = time.strftime("%H:%M:%S")
        self.events.appendleft({
            'time': timestamp,
            'message': message,
            'level': level,
            'timestamp': time.time()
        })
        
    def get_recent(self, count=10):
        return list(self.events)[:count]


# ==============================================================================
# -- Vehicle Status Tracker ----------------------------------------------------
# ==============================================================================

class VehicleStatus:
    """Tracks status of a single vehicle"""
    def __init__(self, actor):
        self.actor = actor
        self.id = actor.id
        self.type_name = get_actor_display_name(actor, truncate=20)
        self.is_hero = actor.attributes.get('role_name') == 'hero'
        self.status = "OK"
        self.lane_id = None
        self.road_id = None
        self.correct_lane_id = None
        self.correct_road_id = None
        self.speed = 0.0
        self.location = None
        self.rotation = None
        self._previous_location = None
        self._previous_location_time = None
        self._previous_sim_time = None
        self.is_in_junction = False
        self.alert_message = None
        self.alert_time = None
        self.last_update = time.time()
        
    def update(self, world_map, sim_time=None):
        """Update vehicle status"""
        try:
            transform = self.actor.get_transform()
            now = time.time()
            previous_location = self.location
            previous_time = self._previous_sim_time if sim_time is not None else self._previous_location_time
            self.location = transform.location
            self.rotation = transform.rotation
            velocity = self.actor.get_velocity()
            velocity_speed = 3.6 * math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)
            delta_speed = self.speed
            if previous_location is not None and previous_time is not None:
                current_time = sim_time if sim_time is not None else now
                dt = current_time - previous_time
                if dt > 1e-4:
                    distance = self.location.distance(previous_location)
                    delta_speed = 3.6 * distance / dt
            self.speed = velocity_speed if velocity_speed > 0.1 else delta_speed
            self._previous_location = self.location
            self._previous_location_time = now
            if sim_time is not None:
                self._previous_sim_time = sim_time
            
            wp = world_map.get_waypoint(self.location)
            if wp:
                self.lane_id = wp.lane_id
                self.road_id = wp.road_id
                self.is_in_junction = wp.is_junction
                
            self.last_update = now
        except:
            pass  # Actor may have been destroyed


class FleetMonitor:
    """Monitors all vehicles in the fleet"""
    def __init__(self, event_log):
        self.vehicles = {}
        self.hero_id = None
        self.selected_id = None  # Currently selected vehicle for monitoring
        self.localization_error = 1.0
        self.alert_active = False
        self.in_opposite_lane_time = None
        self.OPPOSITE_LANE_THRESHOLD_1M = 0.3  # Very fast trigger for 1m accuracy (0.3 seconds)
        self.OPPOSITE_LANE_THRESHOLD_5M = 5.0  # Longer threshold for uncertain position (5 seconds)
        self.DISMISS_COOLDOWN = 5.0  # 5 seconds cooldown before alert can re-trigger
        self.event_log = event_log
        self._world_map = None
        self.hero_stopped = False  # Track if hero was stopped due to alert
        self.beep_thread = None
        self.beep_active = False
        self.dismiss_time = None  # Track when alert was dismissed for cooldown
        
    def set_hero(self, hero_actor, world_map):
        """Explicitly set the hero vehicle"""
        if hero_actor:
            self.hero_id = hero_actor.id
            self.selected_id = hero_actor.id  # Auto-select hero
            self.vehicles[hero_actor.id] = VehicleStatus(hero_actor)
            self.vehicles[hero_actor.id].is_hero = True
            
            wp = world_map.get_waypoint(hero_actor.get_transform().location)
            if wp:
                self.vehicles[hero_actor.id].correct_lane_id = wp.lane_id
                self.vehicles[hero_actor.id].correct_road_id = wp.road_id
                
            self.event_log.add(f"Hero vehicle registered: #{hero_actor.id}", "INFO")
        
    def update_fleet(self, actors, world_map, sim_time=None):
        """Update all vehicle statuses"""
        self._world_map = world_map  # Store for lane checking
        current_ids = set()
        
        for actor in actors:
            if 'vehicle' in actor.type_id:
                current_ids.add(actor.id)
                
                if actor.id not in self.vehicles:
                    self.vehicles[actor.id] = VehicleStatus(actor)
                    
                    if actor.attributes.get('role_name') == 'hero':
                        self.vehicles[actor.id].is_hero = True
                        if self.hero_id is None:
                            self.hero_id = actor.id
                            self.selected_id = actor.id
                            wp = world_map.get_waypoint(actor.get_transform().location)
                            if wp:
                                self.vehicles[actor.id].correct_lane_id = wp.lane_id
                                self.vehicles[actor.id].correct_road_id = wp.road_id
                            
                self.vehicles[actor.id].update(world_map, sim_time)
        
        # Remove destroyed vehicles
        for vid in list(self.vehicles.keys()):
            if vid not in current_ids:
                if vid == self.selected_id:
                    self.selected_id = self.hero_id
                del self.vehicles[vid]
                if vid == self.hero_id:
                    self.hero_id = None
                    
    def select_vehicle(self, vehicle_id):
        """Select a vehicle for detailed monitoring"""
        if vehicle_id in self.vehicles:
            self.selected_id = vehicle_id
            self.event_log.add(f"Selected vehicle #{vehicle_id}", "INFO")
            
    def get_selected(self):
        """Get currently selected vehicle"""
        if self.selected_id and self.selected_id in self.vehicles:
            return self.vehicles[self.selected_id]
        return None
                
    def check_hero_lane(self):
        """Check if hero is in wrong lane by comparing heading to lane direction"""
        if self.hero_id is None or self.hero_id not in self.vehicles:
            return
            
        hero = self.vehicles[self.hero_id]
        
        if hero.location is None or hero.rotation is None:
            return
        
        # IMPORTANT: Skip detection while INSIDE junction - only check on regular roads
        # The alert should trigger AFTER exiting the junction (at the pedestrian crossing)
        if hero.is_in_junction:
            # Reset timer while in junction - don't accumulate time
            self.in_opposite_lane_time = None
            if not self.alert_active:
                hero.status = "OK"
                hero.alert_message = None
            return
            
        # Get waypoint at hero location
        if self._world_map is None:
            return
            
        wp = self._world_map.get_waypoint(hero.location)
        if wp is None:
            return
        
        # Compare vehicle heading to lane direction
        lane_yaw = wp.transform.rotation.yaw
        vehicle_yaw = hero.rotation.yaw
        
        # Calculate angle difference (normalize to -180 to 180)
        angle_diff = vehicle_yaw - lane_yaw
        while angle_diff > 180:
            angle_diff -= 360
        while angle_diff < -180:
            angle_diff += 360
            
        # Different detection logic based on localization accuracy
        # Check if we're still in cooldown period after dismiss
        in_cooldown = self.dismiss_time and (time.time() - self.dismiss_time < self.DISMISS_COOLDOWN)
        
        # Alert file for cross-process communication with v2x_demo_scenario.py
        alert_file = "/tmp/v2x_wrong_lane_alert.lock"
        
        if self.localization_error <= 1.0:
            # 1m accuracy: Use heading to detect wrong way
            going_wrong_way = abs(angle_diff) > 120
            threshold = self.OPPOSITE_LANE_THRESHOLD_1M
            
            if going_wrong_way:
                if self.in_opposite_lane_time is None:
                    self.in_opposite_lane_time = time.time()
                    
                time_in_opposite = time.time() - self.in_opposite_lane_time
                
                if time_in_opposite >= threshold:
                    # Only trigger alert if not in cooldown period
                    if not self.alert_active and not in_cooldown:
                        self.alert_active = True
                        hero.status = "ALERT"
                        hero.alert_message = "Wrong lane detected - STOPPED"
                        hero.alert_time = time.time()
                        self.selected_id = self.hero_id
                        self.event_log.add(f"ALERT: Vehicle #{self.hero_id} in wrong lane - AUTO STOPPED", "ALERT")
                        self._start_beeping()
                        # Create alert file to notify demo scenario
                        try:
                            with open(alert_file, 'w') as f:
                                f.write(str(time.time()))
                        except:
                            pass
                    if not in_cooldown:
                        self.hero_stopped = True
            else:
                # Vehicle is now in correct lane - auto-clear alert if active
                self.in_opposite_lane_time = None
                if self.alert_active:
                    # Auto-clear alert because vehicle is back in correct lane
                    self.alert_active = False
                    self.hero_stopped = False
                    self._stop_beeping()
                    hero.status = "OK"
                    hero.alert_message = None
                    self.event_log.add(f"Alert auto-cleared - Vehicle #{self.hero_id} back in correct lane", "INFO")
                    # Remove alert file
                    try:
                        if os.path.exists(alert_file):
                            os.remove(alert_file)
                    except:
                        pass
                else:
                    hero.status = "OK"
                    hero.alert_message = None
        else:
            # 5m accuracy: Check if uncertainty circle could overlap opposite lanes
            # Lane width is ~3.5m, so 5m uncertainty means we could be in the opposite lane
            # Check if we're close to the center line (where opposite traffic is)
            
            # Get the lane we're in
            current_lane = wp.lane_id
            
            # Check distance to center of road (lane_id sign indicates direction)
            # Positive lane_id = right side, Negative = left side in most CARLA maps
            # We need to check if uncertainty could put us in opposite direction lanes
            
            # Get the opposite lane waypoint
            opposite_wp = None
            if current_lane > 0:
                # We're on right side, check left lanes
                opposite_wp = wp.get_left_lane()
            else:
                # We're on left side, check right lanes
                opposite_wp = wp.get_right_lane()
            
            # Check if uncertainty circle (5m radius) could reach opposite lanes
            position_uncertain = False
            if opposite_wp and opposite_wp.lane_type == carla.LaneType.Driving:
                # Calculate distance to opposite lane center
                dist_to_opposite = hero.location.distance(opposite_wp.transform.location)
                
                # If uncertainty radius (5m) is greater than distance to opposite lane
                # then we could potentially be in the wrong lane
                if self.localization_error >= dist_to_opposite:
                    position_uncertain = True
                    
                    # 5m MODE: NEVER TRIGGER ALERT - position is too uncertain to know for sure
                    # Just show uncertainty status, teleoperator must decide
                    if abs(angle_diff) > 90:
                        # Heading is questionable AND position uncertain
                        if self.in_opposite_lane_time is None:
                            self.in_opposite_lane_time = time.time()
                        
                        time_uncertain = time.time() - self.in_opposite_lane_time
                        
                        # DO NOT trigger alert - just show uncertain status with timer
                        hero.status = "UNCERTAIN"
                        hero.alert_message = f"Position uncertain ({time_uncertain:.1f}s) - Cannot confirm lane"
                    else:
                        self.in_opposite_lane_time = None
                        hero.status = "UNCERTAIN"
                        hero.alert_message = "Position overlaps opposite lane"
            
            if not position_uncertain:
                # Position is OK - auto-clear alert if active
                self.in_opposite_lane_time = None
                if self.alert_active:
                    # Auto-clear alert because position is now certain
                    self.alert_active = False
                    self.hero_stopped = False
                    self._stop_beeping()
                    hero.status = "UNCERTAIN"
                    hero.alert_message = "Low accuracy mode"
                    self.event_log.add(f"Alert auto-cleared - Vehicle #{self.hero_id} position confirmed", "INFO")
                else:
                    hero.status = "UNCERTAIN"
                    hero.alert_message = "Low accuracy mode"
    
    def _start_beeping(self):
        """Start the alert beep sound"""
        if self.beep_active:
            return
        self.beep_active = True
        
        def beep_loop():
            try:
                # Initialize pygame mixer if not already
                if not pygame.mixer.get_init():
                    pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
                
                # Generate beep sound
                import numpy as np
                duration = 0.15  # seconds
                frequency = 880  # Hz (A5 note)
                sample_rate = 22050
                t = np.linspace(0, duration, int(sample_rate * duration), False)
                wave = np.sin(2 * np.pi * frequency * t) * 0.3
                wave = (wave * 32767).astype(np.int16)
                
                # Create sound
                sound = pygame.mixer.Sound(buffer=wave.tobytes())
                
                while self.beep_active and self.alert_active:
                    sound.play()
                    time.sleep(0.5)  # Beep every 0.5 seconds
            except Exception as e:
                print(f"Beep error: {e}")
        
        self.beep_thread = threading.Thread(target=beep_loop, daemon=True)
        self.beep_thread.start()
    
    def _stop_beeping(self):
        """Stop the alert beep sound"""
        self.beep_active = False
                
    def set_localization(self, error):
        """Set localization error"""
        old_error = self.localization_error
        self.localization_error = error
        self.in_opposite_lane_time = None
        if error > 1.0:
            self.alert_active = False
            if self.hero_id and self.hero_id in self.vehicles:
                self.vehicles[self.hero_id].status = "OK"
        self.event_log.add(f"Localization set to {error}m", "INFO")
        
    def reset_hero_lane(self, world_map):
        """Reset hero's correct lane"""
        if self.hero_id and self.hero_id in self.vehicles:
            hero = self.vehicles[self.hero_id]
            wp = world_map.get_waypoint(hero.location)
            if wp:
                hero.correct_lane_id = wp.lane_id
                hero.correct_road_id = wp.road_id
                hero.status = "OK"
                hero.alert_message = None
                self.alert_active = False
                self.in_opposite_lane_time = None
                self.event_log.add(f"Lane reset for #{self.hero_id}: Lane {wp.lane_id}", "INFO")
                
    def clear_alert(self):
        """Clear current alert and allow teleoperator to resume control (for Take Control)"""
        self.alert_active = False
        self.hero_stopped = False  # Allow driving again
        self.in_opposite_lane_time = None
        self._stop_beeping()
        # Remove alert file
        alert_file = "/tmp/v2x_wrong_lane_alert.lock"
        try:
            if os.path.exists(alert_file):
                os.remove(alert_file)
        except:
            pass
        if self.hero_id and self.hero_id in self.vehicles:
            self.vehicles[self.hero_id].status = "OK"
            self.vehicles[self.hero_id].alert_message = None
        self.event_log.add("Alert cleared - Teleoperator has control", "INFO")
    
    def dismiss_alert(self):
        """Dismiss alert with cooldown timer (for Seen/Dismiss button)"""
        self.alert_active = False
        self.hero_stopped = False  # Allow driving again
        self.in_opposite_lane_time = None
        self._stop_beeping()
        self.dismiss_time = time.time()  # Start cooldown timer
        # Remove alert file
        alert_file = "/tmp/v2x_wrong_lane_alert.lock"
        try:
            if os.path.exists(alert_file):
                os.remove(alert_file)
        except:
            pass
        if self.hero_id and self.hero_id in self.vehicles:
            self.vehicles[self.hero_id].status = "OK"
            self.vehicles[self.hero_id].alert_message = None
        self.event_log.add(f"Alert dismissed - {self.DISMISS_COOLDOWN}s cooldown before re-trigger", "INFO")
            
    def get_hero(self):
        """Get hero vehicle"""
        if self.hero_id and self.hero_id in self.vehicles:
            return self.vehicles[self.hero_id]
        return None


# ==============================================================================
# -- Control Panel UI ----------------------------------------------------------
# ==============================================================================

class ControlPanel:
    """Right-side control panel with fleet monitoring"""
    
    def __init__(self, width, height):
        self.width = PANEL_WIDTH
        self.height = height
        self.x_offset = width - self.width
        
        self.surface = pygame.Surface((self.width, self.height))
        self.pulse = 0
        
        # Fonts - LARGER for readability
        self.font_title = pygame.font.SysFont('Arial', 22, bold=True)
        self.font_large = pygame.font.SysFont('Arial', 42, bold=True)
        self.font_med = pygame.font.SysFont('Arial', 16)
        self.font_small = pygame.font.SysFont('Arial', 14)
        self.font_mono = pygame.font.SysFont('Consolas', 14)
        
        # Button
        self.button_rect = None
        self.button_hovered = False
        self.popup_button_rect = None  # For center popup button
        self.teleop_button_rect = None  # Teleop button in panel
        
        # Fleet list scroll
        self.fleet_scroll = 0
        self.fleet_item_rects = {}  # vehicle_id -> rect for click detection
        self.fleet_list_rect = None
        self.fleet_visible_count = 0
        self.current_town = "Town10HD_Opt"
        
        # Popup buttons
        self.popup_button_rect = None  # Take Control button
        self.popup_dismiss_rect = None  # Seen/Dismiss button
    
    def set_current_town(self, town_name):
        """Set the current town name"""
        self.current_town = town_name
    
    def handle_popup_click(self, pos, fleet_monitor, event_log=None):
        """Handle click on the center popup buttons"""
        # Check Take Control button - DON'T clear alert, just open teleop
        # Alert will auto-clear when vehicle is back in correct lane
        if self.popup_button_rect and self.popup_button_rect.collidepoint(pos):
            hero = fleet_monitor.get_hero()
            if hero:
                # Allow driving while in teleop (don't keep auto-stopping)
                fleet_monitor.hero_stopped = False
                # Only launch teleop for the vehicle that was selected from VehicleMap
                self._launch_teleop_station(hero.id)
                if event_log:
                    event_log.add(f"Teleop opened for vehicle #{hero.id} (selected from VehicleMap) - Correct lane to clear alert", "INFO")
            return True
        # Check Seen/Dismiss button - use dismiss_alert with cooldown
        if self.popup_dismiss_rect and self.popup_dismiss_rect.collidepoint(pos):
            fleet_monitor.dismiss_alert()
            if event_log:
                event_log.add("Alert dismissed (5s cooldown)", "INFO")
            return True
        return False
        
    def handle_click(self, pos, fleet_monitor, event_log=None):
        """Handle mouse click"""
        adjusted_pos = (pos[0] - self.x_offset, pos[1])
        
        # Check fleet list clicks
        for vid, rect in self.fleet_item_rects.items():
            if rect.collidepoint(adjusted_pos):
                fleet_monitor.select_vehicle(vid)
                return True
        
        # Check teleop button click - only launch for the selected vehicle
        if self.teleop_button_rect and self.teleop_button_rect.collidepoint(adjusted_pos):
            selected = fleet_monitor.get_selected()
            if selected:
                # Only launch teleop for the vehicle that was selected from VehicleMap
                # This ensures we only spawn cameras on the intended vehicle
                self._launch_teleop_station(selected.id)
                if event_log:
                    event_log.add(f"Teleop opened for vehicle #{selected.id} (selected from VehicleMap)", "INFO")
            return True
            
        return False

    def handle_scroll(self, pos, direction, fleet_monitor):
        """Scroll the fleet list. direction > 0 scrolls up, direction < 0 scrolls down."""
        adjusted_pos = (pos[0] - self.x_offset, pos[1])
        if not self.fleet_list_rect or not self.fleet_list_rect.collidepoint(adjusted_pos):
            return False

        max_scroll = max(0, len(fleet_monitor.vehicles) - max(self.fleet_visible_count, 1))
        if direction > 0:
            self.fleet_scroll = max(0, self.fleet_scroll - 1)
        else:
            self.fleet_scroll = min(max_scroll, self.fleet_scroll + 1)
        return True
        
    def update_hover(self, pos):
        """Update button hover state"""
        adjusted_pos = (pos[0] - self.x_offset, pos[1])
        self.button_hovered = self.button_rect and self.button_rect.collidepoint(adjusted_pos)
        
    def _launch_teleop_station(self, hero_id):
        """Launch full teleoperation station with multiple cameras and steering wheel support"""
        # Ensure hero_id is an integer (CARLA actor IDs are integers)
        try:
            hero_id_int = int(hero_id)
        except (ValueError, TypeError):
            print(f"Error: Invalid vehicle ID: {hero_id}")
            return
        
        print(f"Launching Teleop Station for vehicle CARLA ID: {hero_id_int}...")
        
        # Create lock file to signal teleop is active (main app should not control hero)
        lock_file = "/tmp/carla_teleop_active.lock"
        
        script = f'''#!/usr/bin/env python3
import carla
import pygame
import numpy as np
import math
import time
import os
import atexit
from configparser import ConfigParser

# Lock file to signal teleop ownership
LOCK_FILE = "{lock_file}"

def create_lock():
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    print(f"Teleop lock acquired: {{LOCK_FILE}}")

def remove_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            print(f"Teleop lock released: {{LOCK_FILE}}")
    except:
        pass

# Create lock on start, remove on exit
create_lock()
atexit.register(remove_lock)

# ============================================================================
# TELEOPERATION STATION - Multi-Camera Control Interface with Steering Wheel
# ============================================================================

client = carla.Client("127.0.0.1", 2000)
client.set_timeout(10.0)  # Increased timeout

try:
    world = client.get_world()
    map_name = world.get_map().name
    print(f"✓ Connected to CARLA world: {{map_name}}")
    
    # Verify world is actually connected by getting snapshot
    snapshot = world.get_snapshot()
    print(f"✓ World snapshot timestamp: {{snapshot.timestamp.elapsed_seconds}}s")
except Exception as e:
    print(f"✗ Failed to connect to CARLA: {{e}}")
    print("Make sure CARLA is running and accessible on port 2000")
    exit(1)

# NOTE: Do NOT create traffic manager or change any world settings
# The main script manages the simulation - teleop is just an observer/controller

# Find vehicle by CARLA actor ID
target_vehicle_id = {hero_id_int}  # CARLA actor ID from VehicleMap selection
hero = None

print(f"Searching for vehicle with CARLA ID: {{target_vehicle_id}} (type: {{type(target_vehicle_id).__name__}})")

# Initial wait to allow vehicles to spawn/be available
print("Waiting 2 seconds for vehicles to be available...")
time.sleep(2.0)

# Wait a bit and retry if no vehicles found (they might still be spawning)
max_retries = 10  # Increased retries
retry_delay = 1.0  # seconds
vehicle_actors = []

for attempt in range(max_retries):
    # Search all actors for the vehicle
    all_actors = world.get_actors()
    vehicle_actors = [actor for actor in all_actors if 'vehicle' in actor.type_id]
    
    print(f"Attempt {{attempt + 1}}/{{max_retries}}: Found {{len(vehicle_actors)}} vehicles in simulation")
    
    if len(vehicle_actors) > 0:
        break  # Found vehicles, proceed
    
    if attempt < max_retries - 1:
        print(f"  No vehicles found yet, waiting {{retry_delay}}s before retry...")
        time.sleep(retry_delay)

if len(vehicle_actors) == 0:
    print("\\n✗ ERROR: No vehicles found in simulation after {{max_retries}} attempts!")
    print("Possible causes:")
    print("  1. Simulation is not running")
    print("  2. Vehicles have not been spawned yet")
    print("  3. CARLA server is not connected properly")
    print("\\nPlease ensure:")
    print("  - CARLA is running")
    print("  - Simulation has started and vehicles are spawned")
    print("  - The vehicle with ID {{target_vehicle_id}} exists in the simulation")
    exit(1)

# Try to find by exact ID match (try both int and string comparison)
for actor in vehicle_actors:
    # Try direct ID match
    if actor.id == target_vehicle_id:
        hero = actor
        print(f"✓ Found vehicle: ID={{actor.id}}, Type={{actor.type_id}}")
        break
    # Also try converting to int/string for flexibility
    try:
        if int(actor.id) == int(target_vehicle_id):
            hero = actor
            print(f"✓ Found vehicle (after conversion): ID={{actor.id}}, Type={{actor.type_id}}")
            break
    except (ValueError, TypeError):
        pass

# If not found, list available vehicles for debugging
if not hero:
    print("✗ Vehicle not found! Available vehicles:")
    for actor in vehicle_actors[:20]:  # Show first 20 for better debugging
        print(f"  - ID: {{actor.id}} (type: {{type(actor.id).__name__}}), Type: {{actor.type_id}}")
    if len(vehicle_actors) > 20:
        print(f"  ... and {{len(vehicle_actors) - 20}} more vehicles")
    print(f"\\nError: Vehicle with CARLA ID {{target_vehicle_id}} (type: {{type(target_vehicle_id).__name__}}) not found in simulation")
    print("Make sure the vehicle exists and the simulation is running.")
    print("\\nTip: Check the vehicle IDs above and verify the correct ID is being used.")
    exit(1)

print(f"✓ Connected to vehicle #{{hero.id}} ({{hero.type_id}})")

# Setup pygame
pygame.init()
pygame.font.init()

# Full HD teleoperation display
SCREEN_W, SCREEN_H = 1920, 1080
try:
    display = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.HWSURFACE | pygame.DOUBLEBUF | pygame.RESIZABLE)
except:
    SCREEN_W, SCREEN_H = 1600, 900
    display = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.HWSURFACE | pygame.DOUBLEBUF | pygame.RESIZABLE)

pygame.display.set_caption("V2X TELEOP STATION - Remote Vehicle Control")
clock = pygame.time.Clock()

# Colors
COLOR_BG = (18, 22, 28)
COLOR_PANEL = (28, 34, 42)
COLOR_BORDER = (50, 60, 75)
COLOR_TEXT = (230, 235, 245)
COLOR_MUTED = (100, 110, 125)
COLOR_GREEN = (46, 204, 113)
COLOR_YELLOW = (241, 196, 15)
COLOR_RED = (231, 76, 60)
COLOR_BLUE = (52, 152, 219)
COLOR_CYAN = (26, 188, 156)

# ============================================================================
# Steering Wheel Setup (Logitech G29 / G920 compatible)
# ============================================================================
joystick = None
use_wheel = False
steer_idx = 0
throttle_idx = 2
brake_idx = 3
reverse_idx = 5
handbrake_idx = 4

# Try to initialize steering wheel
pygame.joystick.init()
joystick_count = pygame.joystick.get_count()
if joystick_count > 0:
    joystick = pygame.joystick.Joystick(0)
    joystick.init()
    use_wheel = True
    print(f"Steering wheel connected: {{joystick.get_name()}}")
else:
    print("No steering wheel detected - using keyboard controls")

# Camera setup
bp_lib = world.get_blueprint_library()

def create_camera(name, width, height, fov, transform, fps_tick="0.1"):
    bp = bp_lib.find("sensor.camera.rgb")
    bp.set_attribute("image_size_x", str(width))
    bp.set_attribute("image_size_y", str(height))
    bp.set_attribute("fov", str(fov))
    bp.set_attribute("sensor_tick", fps_tick)
    cam = world.spawn_actor(bp, transform, attach_to=hero)
    return cam

# Camera configurations
# HIGH QUALITY: All cameras at high resolution and 30 FPS for best teleoperation experience
cameras = {{}}
camera_surfaces = {{}}

# Main front camera - Full HD quality
cameras['front'] = create_camera('front', 1920, 1080, 100,
    carla.Transform(carla.Location(x=1.5, z=1.4), carla.Rotation(pitch=-5)),
    fps_tick="0.033")  # 30 FPS

# Rear camera - HD quality
cameras['rear'] = create_camera('rear', 1280, 720, 110,
    carla.Transform(carla.Location(x=-2.5, z=1.2), carla.Rotation(yaw=180, pitch=-10)),
    fps_tick="0.05")  # 20 FPS

# Mirror cameras - Good quality for awareness
cameras['left_mirror'] = create_camera('left_mirror', 640, 360, 60,
    carla.Transform(carla.Location(x=0.5, y=-1.0, z=1.2), carla.Rotation(yaw=-150, pitch=-5)),
    fps_tick="0.066")  # 15 FPS
cameras['right_mirror'] = create_camera('right_mirror', 640, 360, 60,
    carla.Transform(carla.Location(x=0.5, y=1.0, z=1.2), carla.Rotation(yaw=150, pitch=-5)),
    fps_tick="0.066")  # 15 FPS

# Side cameras - HD quality for blind spot monitoring
cameras['left_side'] = create_camera('left_side', 1280, 720, 90,
    carla.Transform(carla.Location(x=0, y=-1.2, z=1.0), carla.Rotation(yaw=-90)),
    fps_tick="0.05")  # 20 FPS
cameras['right_side'] = create_camera('right_side', 1280, 720, 90,
    carla.Transform(carla.Location(x=0, y=1.2, z=1.0), carla.Rotation(yaw=90)),
    fps_tick="0.05")  # 20 FPS

# Image callbacks
def make_callback(name):
    def callback(image):
        array = np.frombuffer(image.raw_data, dtype=np.uint8)
        array = array.reshape((image.height, image.width, 4))
        array = array[:, :, :3]
        array = array[:, :, ::-1]
        camera_surfaces[name] = pygame.surfarray.make_surface(array.swapaxes(0, 1))
    return callback

for name, cam in cameras.items():
    cam.listen(make_callback(name))

# Control state
control = carla.VehicleControl()
steer = 0.0
throttle = 0.0
brake = 0.0
reverse = False
autopilot = False
hand_brake = False

# Fonts
font_large = pygame.font.SysFont('Arial', 36, bold=True)
font_med = pygame.font.SysFont('Arial', 20, bold=True)
font_small = pygame.font.SysFont('Arial', 14)
font_tiny = pygame.font.SysFont('Arial', 11)
font_mono = pygame.font.SysFont('Consolas', 12)

# Exit button rect
exit_button_rect = None

def parse_wheel_input():
    global steer, throttle, brake, hand_brake
    if not use_wheel or joystick is None:
        return False
    
    numAxes = joystick.get_numaxes()
    jsInputs = [float(joystick.get_axis(i)) for i in range(numAxes)]
    jsButtons = [joystick.get_button(i) for i in range(joystick.get_numbuttons())]
    
    K1 = 1.0
    steer = K1 * math.tan(1.1 * jsInputs[steer_idx]) if steer_idx < numAxes else 0
    steer = max(-1.0, min(1.0, steer))
    
    if throttle_idx < numAxes:
        K2 = 1.6
        raw_throttle = jsInputs[throttle_idx]
        throttle_cmd = K2 + (2.05 * math.log10(-0.7 * raw_throttle + 1.4) - 1.2) / 0.92
        throttle = max(0.0, min(1.0, throttle_cmd))
    
    if brake_idx < numAxes:
        raw_brake = jsInputs[brake_idx]
        brake_cmd = 1.6 + (2.05 * math.log10(-0.7 * raw_brake + 1.4) - 1.2) / 0.92
        brake = max(0.0, min(1.0, brake_cmd))
    
    if handbrake_idx < joystick.get_numbuttons():
        hand_brake = bool(jsButtons[handbrake_idx])
    
    return True

def draw_speedometer(surface, x, y, speed, max_speed=120):
    radius = 50  # Smaller radius
    center = (x + radius, y + radius)
    
    pygame.draw.circle(surface, COLOR_PANEL, center, radius)
    pygame.draw.circle(surface, COLOR_BORDER, center, radius, 2)
    
    angle_range = 240
    start_angle = 150
    speed_ratio = min(speed / max_speed, 1.0)
    
    for i in range(0, int(max_speed) + 1, 30):  # Fewer ticks
        tick_ratio = i / max_speed
        angle = math.radians(start_angle - tick_ratio * angle_range)
        inner_r = radius - 8
        outer_r = radius - 3
        x1 = center[0] + inner_r * math.cos(angle)
        y1 = center[1] - inner_r * math.sin(angle)
        x2 = center[0] + outer_r * math.cos(angle)
        y2 = center[1] - outer_r * math.sin(angle)
        pygame.draw.line(surface, COLOR_MUTED, (x1, y1), (x2, y2), 2)
    
    needle_angle = math.radians(start_angle - speed_ratio * angle_range)
    needle_len = radius - 12
    needle_x = center[0] + needle_len * math.cos(needle_angle)
    needle_y = center[1] - needle_len * math.sin(needle_angle)
    needle_color = COLOR_GREEN if speed < 50 else (COLOR_YELLOW if speed < 80 else COLOR_RED)
    pygame.draw.line(surface, needle_color, center, (needle_x, needle_y), 2)
    pygame.draw.circle(surface, needle_color, center, 4)
    
    speed_text = font_med.render(f"{{int(speed)}}", True, COLOR_TEXT)
    surface.blit(speed_text, (center[0] - speed_text.get_width()//2, center[1] + 8))
    
    unit_text = font_tiny.render("km/h", True, COLOR_MUTED)
    surface.blit(unit_text, (center[0] - unit_text.get_width()//2, center[1] + 28))

def draw_steering_indicator(surface, x, y, steer_value):
    width, height = 100, 18  # Smaller
    pygame.draw.rect(surface, COLOR_PANEL, (x, y, width, height), border_radius=3)
    pygame.draw.rect(surface, COLOR_BORDER, (x, y, width, height), 1, border_radius=3)
    
    center_x = x + width // 2
    pygame.draw.line(surface, COLOR_MUTED, (center_x, y + 3), (center_x, y + height - 3), 1)
    
    steer_pos = center_x + int(steer_value * (width // 2 - 6))
    pygame.draw.rect(surface, COLOR_CYAN, (steer_pos - 3, y + 3, 6, height - 6), border_radius=2)
    
    label = font_tiny.render("STEER", True, COLOR_MUTED)
    surface.blit(label, (x + width//2 - label.get_width()//2, y - 12))

def draw_pedals(surface, x, y, throttle_val, brake_val):
    bar_w, bar_h = 18, 55  # Smaller
    gap = 8
    
    pygame.draw.rect(surface, COLOR_PANEL, (x, y, bar_w, bar_h), border_radius=2)
    fill_h = int(throttle_val * bar_h)
    if fill_h > 0:
        pygame.draw.rect(surface, COLOR_GREEN, (x, y + bar_h - fill_h, bar_w, fill_h), border_radius=2)
    pygame.draw.rect(surface, COLOR_BORDER, (x, y, bar_w, bar_h), 1, border_radius=2)
    label = font_tiny.render("T", True, COLOR_TEXT)
    surface.blit(label, (x + bar_w//2 - label.get_width()//2, y + bar_h + 2))
    
    bx = x + bar_w + gap
    pygame.draw.rect(surface, COLOR_PANEL, (bx, y, bar_w, bar_h), border_radius=2)
    fill_h = int(brake_val * bar_h)
    if fill_h > 0:
        pygame.draw.rect(surface, COLOR_RED, (bx, y + bar_h - fill_h, bar_w, fill_h), border_radius=2)
    pygame.draw.rect(surface, COLOR_BORDER, (bx, y, bar_w, bar_h), 1, border_radius=2)
    label = font_tiny.render("B", True, COLOR_TEXT)
    surface.blit(label, (bx + bar_w//2 - label.get_width()//2, y + bar_h + 2))

def draw_status_panel(surface, x, y, w, h):
    pygame.draw.rect(surface, COLOR_PANEL, (x, y, w, h), border_radius=5)
    pygame.draw.rect(surface, COLOR_BORDER, (x, y, w, h), 2, border_radius=5)
    
    title = font_small.render("VEHICLE STATUS", True, COLOR_CYAN)
    surface.blit(title, (x + 10, y + 8))
    
    vel = hero.get_velocity()
    speed = 3.6 * math.sqrt(vel.x**2 + vel.y**2 + vel.z**2)
    transform = hero.get_transform()
    loc = transform.location
    rot = transform.rotation
    
    ty = y + 28
    
    # Basic info
    t = font_mono.render(f"ID: #{{hero.id}}  Gear: {{'R' if reverse else 'D'}}", True, COLOR_TEXT)
    surface.blit(t, (x + 10, ty))
    ty += 16
    
    t = font_mono.render(f"Pos: ({{loc.x:.0f}}, {{loc.y:.0f}})  Hdg: {{rot.yaw:.0f}}deg", True, COLOR_MUTED)
    surface.blit(t, (x + 10, ty))
    ty += 18

def draw_exit_button(surface, x, y, w, h):
    global exit_button_rect
    exit_button_rect = pygame.Rect(x, y, w, h)
    
    mouse_pos = pygame.mouse.get_pos()
    hovered = exit_button_rect.collidepoint(mouse_pos)
    
    btn_color = (180, 60, 50) if hovered else COLOR_RED
    pygame.draw.rect(surface, btn_color, exit_button_rect, border_radius=5)
    pygame.draw.rect(surface, (255, 100, 100), exit_button_rect, 2, border_radius=5)
    
    text = font_med.render("EXIT TELEOP", True, COLOR_TEXT)
    surface.blit(text, (x + w//2 - text.get_width()//2, y + h//2 - text.get_height()//2))

# Main loop
running = True
while running:
    dt = clock.tick(60) / 1000.0
    
    vel = hero.get_velocity()
    current_speed = 3.6 * math.sqrt(vel.x**2 + vel.y**2 + vel.z**2)
    
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1 and exit_button_rect and exit_button_rect.collidepoint(event.pos):
                running = False
        elif event.type == pygame.JOYBUTTONDOWN:
            if event.button == reverse_idx:
                reverse = not reverse
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                running = False
            elif event.key == pygame.K_p:
                autopilot = not autopilot
                hero.set_autopilot(autopilot)  # Use default TM port
                if autopilot:
                    print("Autopilot ON")
                else:
                    print("Autopilot OFF - Manual control")
            elif event.key == pygame.K_r:
                reverse = not reverse
            elif event.key == pygame.K_SPACE:
                hand_brake = True
            elif event.key == pygame.K_q:
                running = False
        elif event.type == pygame.KEYUP:
            if event.key == pygame.K_SPACE:
                hand_brake = False
        elif event.type == pygame.VIDEORESIZE:
            SCREEN_W, SCREEN_H = event.w, event.h
            display = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.HWSURFACE | pygame.DOUBLEBUF | pygame.RESIZABLE)
    
    # Input handling
    if not autopilot:
        if use_wheel:
            parse_wheel_input()
        else:
            keys = pygame.key.get_pressed()
            
            if keys[pygame.K_w] or keys[pygame.K_UP]:
                throttle = min(1.0, throttle + 3.0 * dt)
            else:
                throttle = max(0.0, throttle - 5.0 * dt)
                
            if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                brake = min(1.0, brake + 3.0 * dt)
            else:
                brake = max(0.0, brake - 5.0 * dt)
            
            if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                steer = max(-1.0, steer - 3.0 * dt)
            elif keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                steer = min(1.0, steer + 3.0 * dt)
            else:
                if steer > 0:
                    steer = max(0, steer - 5.0 * dt)
                else:
                    steer = min(0, steer + 5.0 * dt)
        
        control.throttle = throttle
        control.brake = brake
        control.steer = steer
        control.hand_brake = hand_brake
        control.reverse = reverse
        hero.apply_control(control)
    
    # Clear screen
    display.fill(COLOR_BG)
    
    # Layout calculations - balance camera space and panel readability
    # Side panel needs at least 280px for content to fit properly
    min_panel_w = 280
    side_panel_w = max(min_panel_w, int(SCREEN_W * 0.18))
    main_cam_w = SCREEN_W - side_panel_w - 25  # 25px for margins
    main_cam_h = int(SCREEN_H * 0.6)
    
    # === MAIN FRONT CAMERA ===
    main_x, main_y = 10, 10
    pygame.draw.rect(display, COLOR_PANEL, (main_x-2, main_y-2, main_cam_w+4, main_cam_h+4), border_radius=5)
    if 'front' in camera_surfaces:
        scaled = pygame.transform.scale(camera_surfaces['front'], (main_cam_w, main_cam_h))
        display.blit(scaled, (main_x, main_y))
    
    # === DASHCAM OVERLAY ===
    # Recording indicator (top-left)
    rec_radius = 8
    rec_x, rec_y = main_x + 15, main_y + 20
    rec_flash = (time.time() * 2) % 2 < 1.5  # Flash effect
    if rec_flash:
        pygame.draw.circle(display, COLOR_RED, (rec_x, rec_y), rec_radius)
    pygame.draw.circle(display, (255, 100, 100), (rec_x, rec_y), rec_radius, 2)
    rec_text = font_tiny.render("REC", True, COLOR_RED if rec_flash else (180, 80, 80))
    display.blit(rec_text, (rec_x + 14, rec_y - 6))
    
    # Timestamp overlay (top-right of front camera)
    timestamp = time.strftime("%Y-%m-%d  %H:%M:%S")
    ts_font = pygame.font.SysFont('Consolas', 14)
    ts_text = ts_font.render(timestamp, True, COLOR_TEXT)
    ts_bg = pygame.Surface((ts_text.get_width() + 10, ts_text.get_height() + 4), pygame.SRCALPHA)
    ts_bg.fill((0, 0, 0, 160))
    display.blit(ts_bg, (main_x + main_cam_w - ts_text.get_width() - 15, main_y + 8))
    display.blit(ts_text, (main_x + main_cam_w - ts_text.get_width() - 10, main_y + 10))
    
    # Speed overlay (bottom-left of front camera)
    vel = hero.get_velocity()
    cam_speed = 3.6 * math.sqrt(vel.x**2 + vel.y**2 + vel.z**2)
    speed_font = pygame.font.SysFont('Arial', 28, bold=True)
    speed_str = f"{{int(cam_speed)}} km/h"
    speed_text = speed_font.render(speed_str, True, COLOR_GREEN if cam_speed < 50 else (COLOR_YELLOW if cam_speed < 80 else COLOR_RED))
    speed_bg = pygame.Surface((speed_text.get_width() + 12, speed_text.get_height() + 4), pygame.SRCALPHA)
    speed_bg.fill((0, 0, 0, 160))
    display.blit(speed_bg, (main_x + 8, main_y + main_cam_h - speed_text.get_height() - 95))
    display.blit(speed_text, (main_x + 14, main_y + main_cam_h - speed_text.get_height() - 93))
    
    # GPS coordinates (bottom-left, below speed)
    loc = hero.get_transform().location
    gps_font = pygame.font.SysFont('Consolas', 11)
    gps_str = f"GPS: {{loc.x:.1f}}, {{loc.y:.1f}}"
    gps_text = gps_font.render(gps_str, True, COLOR_MUTED)
    gps_bg = pygame.Surface((gps_text.get_width() + 8, gps_text.get_height() + 2), pygame.SRCALPHA)
    gps_bg.fill((0, 0, 0, 140))
    display.blit(gps_bg, (main_x + 8, main_y + main_cam_h - 65))
    display.blit(gps_text, (main_x + 12, main_y + main_cam_h - 64))
    
    # Vehicle ID (top center)
    id_font = pygame.font.SysFont('Arial', 12)
    id_text = id_font.render(f"V2X TELEOP | Vehicle #{{hero.id}}", True, (180, 190, 200))
    id_bg = pygame.Surface((id_text.get_width() + 16, id_text.get_height() + 4), pygame.SRCALPHA)
    id_bg.fill((0, 0, 0, 140))
    display.blit(id_bg, (main_x + main_cam_w//2 - id_text.get_width()//2 - 8, main_y + 8))
    display.blit(id_text, (main_x + main_cam_w//2 - id_text.get_width()//2, main_y + 10))
    
    # === SIDE MIRRORS ===
    mirror_w, mirror_h = 160, 90
    
    lm_x = main_x + 5
    lm_y = main_y + main_cam_h - mirror_h - 5
    pygame.draw.rect(display, (0,0,0), (lm_x-2, lm_y-2, mirror_w+4, mirror_h+4), border_radius=3)
    if 'left_mirror' in camera_surfaces:
        scaled = pygame.transform.scale(camera_surfaces['left_mirror'], (mirror_w, mirror_h))
        display.blit(scaled, (lm_x, lm_y))
    mirror_label = font_tiny.render("L MIRROR", True, COLOR_YELLOW)
    display.blit(mirror_label, (lm_x + 5, lm_y + 5))
    
    rm_x = main_x + main_cam_w - mirror_w - 5
    rm_y = lm_y
    pygame.draw.rect(display, (0,0,0), (rm_x-2, rm_y-2, mirror_w+4, mirror_h+4), border_radius=3)
    if 'right_mirror' in camera_surfaces:
        scaled = pygame.transform.scale(camera_surfaces['right_mirror'], (mirror_w, mirror_h))
        display.blit(scaled, (rm_x, rm_y))
    mirror_label = font_tiny.render("R MIRROR", True, COLOR_YELLOW)
    display.blit(mirror_label, (rm_x + mirror_w - 55, rm_y + 5))
    
    # === BOTTOM ROW: Side cameras + Rear ===
    bottom_y = main_y + main_cam_h + 10
    bottom_h = SCREEN_H - bottom_y - 10
    cam_gap = 8
    single_cam_w = (main_cam_w - 2 * cam_gap) // 3
    
    ls_x = main_x
    pygame.draw.rect(display, COLOR_PANEL, (ls_x-2, bottom_y-2, single_cam_w+4, bottom_h+4), border_radius=5)
    if 'left_side' in camera_surfaces:
        scaled = pygame.transform.scale(camera_surfaces['left_side'], (single_cam_w, bottom_h))
        display.blit(scaled, (ls_x, bottom_y))
    label = font_tiny.render("LEFT SIDE", True, COLOR_TEXT)
    display.blit(label, (ls_x + 8, bottom_y + 6))
    
    rear_x = ls_x + single_cam_w + cam_gap
    pygame.draw.rect(display, COLOR_PANEL, (rear_x-2, bottom_y-2, single_cam_w+4, bottom_h+4), border_radius=5)
    if 'rear' in camera_surfaces:
        scaled = pygame.transform.scale(camera_surfaces['rear'], (single_cam_w, bottom_h))
        display.blit(scaled, (rear_x, bottom_y))
    label = font_tiny.render("REAR", True, COLOR_TEXT)
    display.blit(label, (rear_x + 8, bottom_y + 6))
    
    rs_x = rear_x + single_cam_w + cam_gap
    pygame.draw.rect(display, COLOR_PANEL, (rs_x-2, bottom_y-2, single_cam_w+4, bottom_h+4), border_radius=5)
    if 'right_side' in camera_surfaces:
        scaled = pygame.transform.scale(camera_surfaces['right_side'], (single_cam_w, bottom_h))
        display.blit(scaled, (rs_x, bottom_y))
    label = font_tiny.render("RIGHT SIDE", True, COLOR_TEXT)
    display.blit(label, (rs_x + 8, bottom_y + 6))
    
    # === RIGHT PANEL ===
    panel_x = main_x + main_cam_w + 15
    panel_y = 10
    
    pygame.draw.rect(display, COLOR_PANEL, (panel_x, panel_y, side_panel_w, 45), border_radius=5)
    header = font_med.render("TELEOP STATION", True, COLOR_CYAN)
    display.blit(header, (panel_x + side_panel_w//2 - header.get_width()//2, panel_y + 10))
    
    # Time
    time_text = font_small.render(time.strftime("%H:%M:%S"), True, COLOR_TEXT)
    display.blit(time_text, (panel_x + side_panel_w - time_text.get_width() - 10, panel_y + 14))
    
    status_y = panel_y + 55
    
    # Speedometer (compact)
    vel = hero.get_velocity()
    speed = 3.6 * math.sqrt(vel.x**2 + vel.y**2 + vel.z**2)
    draw_speedometer(display, panel_x + 5, status_y, speed)
    
    # Steering + Pedals next to speedometer (fit within panel width)
    steering_x = min(panel_x + 115, panel_x + side_panel_w - 110)
    draw_steering_indicator(display, steering_x, status_y + 15, steer)
    draw_pedals(display, steering_x + 15, status_y + 45, throttle, brake)
    
    # Vehicle status panel (needs ~65px)
    status_panel_y = status_y + 115
    status_panel_h = 65
    draw_status_panel(display, panel_x, status_panel_y, side_panel_w, status_panel_h)
    
    # Mode indicator
    mode_y = status_panel_y + status_panel_h + 10
    device_text = "AUTOPILOT" if autopilot else ("WHEEL" if use_wheel else "KEYBOARD")
    device_color = COLOR_GREEN if autopilot else (COLOR_CYAN if use_wheel else COLOR_BLUE)
    pygame.draw.rect(display, device_color, (panel_x, mode_y, side_panel_w, 25), border_radius=4)
    mode_label = font_tiny.render(device_text, True, COLOR_TEXT)
    display.blit(mode_label, (panel_x + side_panel_w//2 - mode_label.get_width()//2, mode_y + 5))
    
    # Controls hint
    hint_y = mode_y + 28
    hints = "ESC=Exit P=Auto R=Rev"
    hint_text = font_tiny.render(hints, True, COLOR_MUTED)
    display.blit(hint_text, (panel_x + 5, hint_y))
    
    # EXIT BUTTON - position at bottom of screen
    exit_btn_y = SCREEN_H - 45
    draw_exit_button(display, panel_x, exit_btn_y, side_panel_w, 35)
    
    pygame.display.flip()

# Cleanup
print("Closing teleop station...")
for cam in cameras.values():
    cam.stop()
    cam.destroy()

# Re-enable autopilot when exiting teleop
try:
    hero.set_autopilot(True)
    print(f"Autopilot re-enabled for vehicle #{{hero.id}}")
except Exception as e:
    print(f"Warning: Could not re-enable autopilot: {{e}}")

# Remove teleop lock file
import os
lock_file = "/tmp/carla_teleop_active.lock"
if os.path.exists(lock_file):
    os.remove(lock_file)
    print("Teleop lock released")

pygame.quit()
print("Teleop station closed")
'''
        # Write temp script and run it
        script_path = "/tmp/carla_teleop_station.py"
        with open(script_path, "w") as f:
            f.write(script)
        
        # Use bash -c with exec to properly run the script and keep terminal open on error
        cmd = f'gnome-terminal --geometry=200x50 -- bash -c "python3 {script_path}; echo Press Enter to close; read"'
        try:
            subprocess.Popen(cmd, shell=True)
        except Exception as e:
            print(f"Launch failed: {e}")
            
    def _launch_3d_view(self, hero_id):
        """Launch 3D manual control view for existing hero vehicle"""
        print(f"Launching 3D view for hero #{hero_id}...")
        script = f'''
import carla
import pygame
import numpy as np

client = carla.Client("127.0.0.1", 2000)
client.set_timeout(5.0)
world = client.get_world()

# Find hero vehicle by ID
hero = None
for actor in world.get_actors():
    if actor.id == {hero_id}:
        hero = actor
        break

if not hero:
    print("Hero vehicle not found!")
    exit(1)

print(f"Attached to hero #{{hero.id}}")

# Setup pygame
pygame.init()
display = pygame.display.set_mode((1280, 720), pygame.HWSURFACE | pygame.DOUBLEBUF)
pygame.display.set_caption("Hero Vehicle Camera - V2X Teleoperator View")
clock = pygame.time.Clock()

# Spawn camera
bp_lib = world.get_blueprint_library()
camera_bp = bp_lib.find("sensor.camera.rgb")
camera_bp.set_attribute("image_size_x", "1280")
camera_bp.set_attribute("image_size_y", "720")
camera_bp.set_attribute("fov", "90")

camera_transform = carla.Transform(carla.Location(x=-5.5, z=2.5), carla.Rotation(pitch=-15))
camera = world.spawn_actor(camera_bp, camera_transform, attach_to=hero)

# Image callback
image_surface = None
def process_image(image):
    global image_surface
    array = np.frombuffer(image.raw_data, dtype=np.uint8)
    array = array.reshape((image.height, image.width, 4))
    array = array[:, :, :3]
    array = array[:, :, ::-1]
    image_surface = pygame.surfarray.make_surface(array.swapaxes(0, 1))

camera.listen(process_image)

# Control
control = carla.VehicleControl()
steer = 0.0
autopilot = False

running = True
while running:
    clock.tick(60)
    
    for event in pygame.event.get():
        if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_p:
                autopilot = not autopilot
                hero.set_autopilot(autopilot)
                print(f"Autopilot: {{'ON' if autopilot else 'OFF'}}")
    
    if not autopilot:
        keys = pygame.key.get_pressed()
        control.throttle = 1.0 if keys[pygame.K_w] or keys[pygame.K_UP] else 0.0
        control.brake = 1.0 if keys[pygame.K_s] or keys[pygame.K_DOWN] else 0.0
        control.hand_brake = keys[pygame.K_SPACE]
        
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            steer = max(-0.7, steer - 0.05)
        elif keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            steer = min(0.7, steer + 0.05)
        else:
            steer = 0.0
        control.steer = steer
        hero.apply_control(control)
    
    if image_surface:
        display.blit(image_surface, (0, 0))
    
    # HUD
    font = pygame.font.SysFont("Arial", 20)
    vel = hero.get_velocity()
    speed = 3.6 * (vel.x**2 + vel.y**2 + vel.z**2)**0.5
    
    # Background bar
    pygame.draw.rect(display, (0, 0, 0, 180), (0, 0, 1280, 40))
    
    status = "AUTOPILOT" if autopilot else "MANUAL"
    status_color = (46, 204, 113) if autopilot else (52, 152, 219)
    
    text = font.render(f"Speed: {{speed:.1f}} km/h  |  Mode: {{status}}  |  WASD=Drive  P=Autopilot  ESC=Close", True, (255,255,255))
    display.blit(text, (10, 10))
    
    # Status indicator
    pygame.draw.circle(display, status_color, (1250, 20), 10)
    
    pygame.display.flip()

camera.stop()
camera.destroy()
pygame.quit()
print("3D view closed")
'''
        # Write temp script and run it
        script_path = "/tmp/carla_hero_view.py"
        with open(script_path, "w") as f:
            f.write(script)
        
        cmd = f'gnome-terminal -- python3 {script_path}'
        try:
            subprocess.Popen(cmd, shell=True)
        except Exception as e:
            print(f"Launch failed: {e}")
            
    def render(self, display, fleet_monitor, event_log):
        """Render the control panel"""
        self.surface.fill(COLOR_BG_DARK)
        self.pulse = (self.pulse + 0.08) % (2 * math.pi)
        self.fleet_item_rects.clear()
        
        y = 0
        
        # === HEADER ===
        pygame.draw.rect(self.surface, COLOR_PANEL_HEADER, (0, 0, self.width, 55))
        pygame.draw.line(self.surface, COLOR_BORDER, (0, 55), (self.width, 55), 2)
        
        title = self.font_title.render("FLEET TELEOP MONITOR", True, COLOR_TEXT_PRIMARY)
        self.surface.blit(title, (15, 10))
        
        # Date and Time
        date_str = time.strftime("%d %b %Y")
        time_str = time.strftime("%H:%M:%S")
        datetime_text = self.font_small.render(f"{date_str}  {time_str}", True, COLOR_TEXT_SECONDARY)
        self.surface.blit(datetime_text, (15, 34))
        
        # Status indicator
        status_color = COLOR_STATUS_ALERT if fleet_monitor.alert_active else COLOR_STATUS_OK
        pygame.draw.circle(self.surface, status_color, (self.width - 30, 28), 10)
        
        y = 65
        
        # === SELECTED VEHICLE ===
        y = self._render_section_header(y, "SELECTED VEHICLE")
        
        selected = fleet_monitor.get_selected()
        pygame.draw.rect(self.surface, COLOR_PANEL_BG, (10, y, self.width - 20, 92))
        
        if selected:
            # ID and type
            id_color = COLOR_SELECTED if selected.id == fleet_monitor.selected_id else COLOR_TEXT_PRIMARY
            text = self.font_med.render(f"#{selected.id} - {selected.type_name}", True, id_color)
            self.surface.blit(text, (20, y + 8))
            
            # Hero badge - position it inside the panel
            if selected.is_hero:
                badge_w = 50
                pygame.draw.rect(self.surface, COLOR_ACCENT_CYAN, (self.width - 30 - badge_w, y + 8, badge_w, 16), border_radius=3)
                text = self.font_small.render("HERO", True, COLOR_WHITE)
                self.surface.blit(text, (self.width - 30 - badge_w + 10, y + 10))
            
            # Speed
            text = self.font_small.render(f"Speed: {selected.speed:.1f} km/h", True, COLOR_TEXT_SECONDARY)
            self.surface.blit(text, (20, y + 28))
            
            # Position
            if selected.location:
                text = self.font_small.render(f"Pos: ({selected.location.x:.1f}, {selected.location.y:.1f})", True, COLOR_TEXT_MUTED)
                self.surface.blit(text, (130, y + 28))
            
            # Junction indicator
            if selected.is_in_junction:
                text = self.font_small.render("IN JUNCTION", True, COLOR_ACCENT_CYAN)
                self.surface.blit(text, (self.width - 90, y + 45))
            
            # Status
            status_colors = {
                "OK": COLOR_STATUS_OK,
                "WARNING": COLOR_STATUS_WARNING,
                "ALERT": COLOR_STATUS_ALERT,
                "UNCERTAIN": COLOR_STATUS_UNCERTAIN
            }
            status_color = status_colors.get(selected.status, COLOR_STATUS_OK)
            
            pygame.draw.rect(self.surface, status_color, (20, y + 58, 70, 20), border_radius=3)
            text = self.font_small.render(selected.status, True, COLOR_WHITE)
            self.surface.blit(text, (28, y + 60))
            
            # TELEOP BUTTON - allow teleoperation of any selected vehicle
            btn_w, btn_h = 120, 28
            btn_x = self.width - btn_w - 25
            btn_y = y + 55
            self.teleop_button_rect = pygame.Rect(btn_x, btn_y, btn_w, btn_h)
            
            mouse_pos = pygame.mouse.get_pos()
            adjusted_mouse = (mouse_pos[0] - self.x_offset, mouse_pos[1])
            btn_hovered = self.teleop_button_rect.collidepoint(adjusted_mouse)
            
            btn_color = COLOR_ACCENT_CYAN if btn_hovered else COLOR_ACCENT_BLUE
            pygame.draw.rect(self.surface, btn_color, self.teleop_button_rect, border_radius=5)
            pygame.draw.rect(self.surface, COLOR_WHITE, self.teleop_button_rect, 2, border_radius=5)
            
            btn_text = self.font_small.render("TELEOP", True, COLOR_WHITE)
            self.surface.blit(btn_text, (btn_x + (btn_w - btn_text.get_width()) // 2, btn_y + 6))
        else:
            text = self.font_med.render("No vehicle selected", True, COLOR_TEXT_MUTED)
            self.surface.blit(text, (20, y + 38))
            self.teleop_button_rect = None
            
        y += 102
        
        # === FLEET LIST ===
        y = self._render_section_header(y, f"FLEET ({len(fleet_monitor.vehicles)} vehicles)")
        
        log_height = 120
        controls_height = 50
        list_height = max(180, self.height - y - log_height - controls_height - 45)
        pygame.draw.rect(self.surface, COLOR_PANEL_BG, (10, y, self.width - 20, list_height))
        self.fleet_list_rect = pygame.Rect(10, y, self.width - 20, list_height)
        
        item_height = 28
        visible_y = y + 3
        
        # Sort: hero first, then selected, then by ID
        sorted_vehicles = sorted(fleet_monitor.vehicles.values(), 
                                key=lambda v: (not v.is_hero, v.id != fleet_monitor.selected_id, v.id))
        
        max_visible = max(1, (list_height - 6) // item_height)
        self.fleet_visible_count = max_visible
        max_scroll = max(0, len(sorted_vehicles) - max_visible)
        self.fleet_scroll = max(0, min(self.fleet_scroll, max_scroll))

        for vehicle in sorted_vehicles[self.fleet_scroll:self.fleet_scroll + max_visible]:
            if visible_y + item_height > y + list_height - 3:
                break
            
            # Store rect for click detection
            item_rect = pygame.Rect(15, visible_y, self.width - 30, item_height - 2)
            self.fleet_item_rects[vehicle.id] = item_rect
                
            # Background for selected/hero
            if vehicle.id == fleet_monitor.selected_id:
                pygame.draw.rect(self.surface, (40, 70, 100), item_rect)
            elif vehicle.is_hero:
                pygame.draw.rect(self.surface, (40, 55, 70), item_rect)
                
            # Status dot
            status_colors = {
                "OK": COLOR_STATUS_OK,
                "WARNING": COLOR_STATUS_WARNING,
                "ALERT": COLOR_STATUS_ALERT,
                "UNCERTAIN": COLOR_STATUS_UNCERTAIN
            }
            dot_color = status_colors.get(vehicle.status, COLOR_STATUS_OK)
            
            if vehicle.status in ["ALERT", "UNCERTAIN"]:
                pulse_intensity = (math.sin(self.pulse * 3) + 1) / 2
                dot_color = pygame.Color(
                    int(dot_color.r * (0.5 + 0.5 * pulse_intensity)),
                    int(dot_color.g * (0.5 + 0.5 * pulse_intensity)),
                    int(dot_color.b * (0.5 + 0.5 * pulse_intensity))
                )
            
            pygame.draw.circle(self.surface, dot_color, (28, visible_y + 11), 4)
            
            # ID
            id_color = COLOR_TEXT_PRIMARY if vehicle.is_hero or vehicle.id == fleet_monitor.selected_id else COLOR_TEXT_SECONDARY
            text = self.font_mono.render(f"#{vehicle.id}", True, id_color)
            self.surface.blit(text, (40, visible_y + 4))
            
            # Type
            text = self.font_small.render(vehicle.type_name[:10], True, COLOR_TEXT_MUTED)
            self.surface.blit(text, (90, visible_y + 4))
            
            # Speed
            text = self.font_small.render(f"{vehicle.speed:.0f}km/h", True, COLOR_TEXT_MUTED)
            self.surface.blit(text, (self.width - 65, visible_y + 4))
            
            visible_y += item_height

        if max_scroll > 0:
            track_x = self.width - 18
            track_y = y + 6
            track_h = list_height - 12
            pygame.draw.rect(self.surface, COLOR_BORDER, (track_x, track_y, 4, track_h), border_radius=2)
            thumb_h = max(18, int(track_h * (max_visible / len(sorted_vehicles))))
            thumb_y = track_y + int((track_h - thumb_h) * (self.fleet_scroll / max_scroll))
            pygame.draw.rect(self.surface, COLOR_ACCENT_BLUE, (track_x, thumb_y, 4, thumb_h), border_radius=2)
            
        y += list_height + 5
        
        # === EVENT LOG ===
        y = self._render_section_header(y, "EVENT LOG")
        
        pygame.draw.rect(self.surface, COLOR_PANEL_BG, (10, y, self.width - 20, log_height))
        
        events = event_log.get_recent(5)
        event_y = y + 8
        for event in events:
            if event_y + 20 > y + log_height:
                break
                
            level_colors = {
                "INFO": COLOR_TEXT_MUTED,
                "WARNING": COLOR_STATUS_WARNING,
                "ALERT": COLOR_STATUS_ALERT
            }
            color = level_colors.get(event['level'], COLOR_TEXT_MUTED)
            
            # Time
            text = self.font_small.render(event['time'], True, COLOR_TEXT_MUTED)
            self.surface.blit(text, (15, event_y))
            
            # Message (truncate if needed)
            msg = event['message'][:32] + "..." if len(event['message']) > 32 else event['message']
            text = self.font_small.render(msg, True, color)
            self.surface.blit(text, (75, event_y))
            
            event_y += 22
            
        y += log_height + 5
        
        # === CONTROLS ===
        self._render_controls()
        
        display.blit(self.surface, (self.x_offset, 0))
        
        # === CENTER ALERT POPUP (rendered on main display, not panel) ===
        if fleet_monitor.alert_active:
            self._render_center_alert_popup(display, fleet_monitor)
        
    def _render_section_header(self, y, title):
        pygame.draw.rect(self.surface, COLOR_PANEL_HEADER, (10, y, self.width - 20, 25))
        text = self.font_small.render(title, True, COLOR_ACCENT_BLUE)
        self.surface.blit(text, (18, y + 5))
        return y + 28
    
    def _render_center_alert_popup(self, display, fleet_monitor):
        """Render a center-screen alert popup to grab teleoperator attention"""
        hero = fleet_monitor.get_hero()
        if not hero:
            return
        
        # Get display dimensions
        display_w = display.get_width()
        display_h = display.get_height()
        
        # Popup dimensions - made taller for two buttons
        popup_w = 520
        popup_h = 260
        popup_x = (display_w - PANEL_WIDTH - popup_w) // 2  # Center in map area
        popup_y = (display_h - popup_h) // 2
        
        # Flashing effect
        flash_on = (time.time() * 3) % 2 < 1
        pulse_alpha = int(180 + 40 * math.sin(self.pulse * 2))
        
        # Dark overlay on the map area
        overlay = pygame.Surface((display_w - PANEL_WIDTH, display_h), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 120))
        display.blit(overlay, (0, 0))
        
        # Popup background
        popup_surf = pygame.Surface((popup_w, popup_h), pygame.SRCALPHA)
        popup_surf.fill((30, 30, 35, pulse_alpha))
        display.blit(popup_surf, (popup_x, popup_y))
        
        # Flashing border
        border_color = COLOR_STATUS_ALERT if flash_on else pygame.Color(150, 50, 40)
        pygame.draw.rect(display, border_color, (popup_x, popup_y, popup_w, popup_h), 4)
        
        # Warning icon (triangle)
        icon_size = 50
        icon_x = popup_x + 30
        icon_y = popup_y + 30
        if flash_on:
            points = [
                (icon_x + icon_size // 2, icon_y),
                (icon_x, icon_y + icon_size),
                (icon_x + icon_size, icon_y + icon_size)
            ]
            pygame.draw.polygon(display, COLOR_STATUS_ALERT, points)
            pygame.draw.polygon(display, COLOR_WHITE, points, 2)
            # Exclamation mark
            font_icon = pygame.font.SysFont('Arial', 32, bold=True)
            exclaim = font_icon.render("!", True, COLOR_WHITE)
            display.blit(exclaim, (icon_x + icon_size // 2 - 5, icon_y + 12))
        
        # Title
        font_title = pygame.font.SysFont('Arial', 28, bold=True)
        title_color = COLOR_STATUS_ALERT if flash_on else pygame.Color(200, 100, 90)
        title = font_title.render("INTERVENTION REQUIRED", True, title_color)
        display.blit(title, (popup_x + 100, popup_y + 25))
        
        # Message
        font_msg = pygame.font.SysFont('Arial', 18)
        if hero.alert_message:
            msg = font_msg.render(hero.alert_message, True, COLOR_TEXT_PRIMARY)
            display.blit(msg, (popup_x + 100, popup_y + 60))
        
        # Vehicle info
        info = font_msg.render(f"Vehicle #{hero.id} - {hero.type_name}", True, COLOR_TEXT_SECONDARY)
        display.blit(info, (popup_x + 100, popup_y + 85))
        
        # Duration
        if hero.alert_time:
            elapsed = time.time() - hero.alert_time
            duration = font_msg.render(f"Duration: {elapsed:.1f}s", True, COLOR_STATUS_WARNING)
            display.blit(duration, (popup_x + 100, popup_y + 110))
        
        # === TWO BUTTONS ===
        btn_w, btn_h = 200, 45
        btn_gap = 20
        total_btn_width = btn_w * 2 + btn_gap
        btn_start_x = popup_x + (popup_w - total_btn_width) // 2
        btn_y = popup_y + popup_h - 100
        
        mouse_pos = pygame.mouse.get_pos()
        font_btn = pygame.font.SysFont('Arial', 16, bold=True)
        
        # --- TAKE CONTROL Button (Green/Cyan) ---
        take_ctrl_x = btn_start_x
        self.popup_button_rect = pygame.Rect(take_ctrl_x, btn_y, btn_w, btn_h)
        take_hovered = self.popup_button_rect.collidepoint(mouse_pos)
        
        take_color = (30, 200, 150) if take_hovered else COLOR_ACCENT_CYAN
        pygame.draw.rect(display, take_color, self.popup_button_rect, border_radius=6)
        pygame.draw.rect(display, COLOR_WHITE, self.popup_button_rect, 2, border_radius=6)
        
        btn_text = font_btn.render("TAKE CONTROL", True, COLOR_WHITE)
        display.blit(btn_text, (take_ctrl_x + (btn_w - btn_text.get_width()) // 2, btn_y + 8))
        sub_text = pygame.font.SysFont('Arial', 11).render("Open Teleop Station", True, (200, 255, 240))
        display.blit(sub_text, (take_ctrl_x + (btn_w - sub_text.get_width()) // 2, btn_y + 28))
        
        # --- SEEN / DISMISS Button (Gray/Muted) ---
        dismiss_x = btn_start_x + btn_w + btn_gap
        self.popup_dismiss_rect = pygame.Rect(dismiss_x, btn_y, btn_w, btn_h)
        dismiss_hovered = self.popup_dismiss_rect.collidepoint(mouse_pos)
        
        dismiss_color = (80, 90, 100) if dismiss_hovered else (55, 65, 75)
        pygame.draw.rect(display, dismiss_color, self.popup_dismiss_rect, border_radius=6)
        pygame.draw.rect(display, COLOR_BORDER, self.popup_dismiss_rect, 2, border_radius=6)
        
        btn_text = font_btn.render("SEEN / DISMISS", True, COLOR_TEXT_PRIMARY)
        display.blit(btn_text, (dismiss_x + (btn_w - btn_text.get_width()) // 2, btn_y + 8))
        sub_text = pygame.font.SysFont('Arial', 11).render("5s cooldown on re-trigger", True, COLOR_TEXT_MUTED)
        display.blit(sub_text, (dismiss_x + (btn_w - sub_text.get_width()) // 2, btn_y + 28))
        
        # Instructions
        font_small = pygame.font.SysFont('Arial', 12)
        hint = font_small.render("C = Take Control  |  D = Dismiss", True, COLOR_TEXT_MUTED)
        display.blit(hint, (popup_x + (popup_w - hint.get_width()) // 2, popup_y + popup_h - 25))
        
    def _render_alert_section(self, y, fleet_monitor):
        """Legacy alert section - kept for reference but not used"""
        pass
        
    def _render_controls(self):
        y = self.height - 50
        pygame.draw.rect(self.surface, COLOR_PANEL_HEADER, (0, y, self.width, 50))
        pygame.draw.line(self.surface, COLOR_BORDER, (0, y), (self.width, y), 1)
        
        controls = [
            "1=1m  5=5m  R=Reset  C=Teleop  D=Dismiss  T=Traffic  V=3D",
            "WASD=Drive  P=Autopilot  Click=Select"
        ]
        for i, ctrl in enumerate(controls):
            text = self.font_small.render(ctrl, True, COLOR_TEXT_MUTED)
            self.surface.blit(text, (15, y + 8 + i * 16))


# ==============================================================================
# -- Map Rendering -------------------------------------------------------------
# ==============================================================================

class MapImage:
    """Renders the 2D map"""
    def __init__(self, carla_world, carla_map, pixels_per_meter):
        self._pixels_per_meter = pixels_per_meter
        self.scale = 1.0

        waypoints = carla_map.generate_waypoints(2)
        margin = 50
        max_x = max(waypoints, key=lambda x: x.transform.location.x).transform.location.x + margin
        max_y = max(waypoints, key=lambda x: x.transform.location.y).transform.location.y + margin
        min_x = min(waypoints, key=lambda x: x.transform.location.x).transform.location.x - margin
        min_y = min(waypoints, key=lambda x: x.transform.location.y).transform.location.y - margin

        self.width = max(max_x - min_x, max_y - min_y)
        self._world_offset = (min_x, min_y)

        width_in_pixels = (1 << 14) - 1
        surface_pixel_per_meter = int(width_in_pixels / self.width)
        if surface_pixel_per_meter > PIXELS_PER_METER:
            surface_pixel_per_meter = PIXELS_PER_METER

        self._pixels_per_meter = surface_pixel_per_meter
        width_in_pixels = int(self._pixels_per_meter * self.width)

        self.big_map_surface = pygame.Surface((width_in_pixels, width_in_pixels)).convert()

        opendrive_content = carla_map.to_opendrive()
        hash_func = hashlib.sha1()
        hash_func.update(opendrive_content.encode("UTF-8"))
        opendrive_hash = str(hash_func.hexdigest())

        filename = carla_map.name.split('/')[-1] + "_" + opendrive_hash + ".tga"
        dirname = os.path.join("cache", "no_rendering_mode")
        full_path = str(os.path.join(dirname, filename))

        cache_valid = False
        if os.path.isfile(full_path):
            # Check if cache file is valid (not too small)
            file_size = os.path.getsize(full_path)
            if file_size > 10000:  # Should be at least 10KB for a real map
                try:
                    self.big_map_surface = pygame.image.load(full_path)
                    cache_valid = True
                    print(f"Loaded cached map: {full_path}")
                except Exception as e:
                    print(f"Failed to load cached map: {e}")
                    cache_valid = False
        
        if not cache_valid:
            print("Generating new map (this may take a moment)...")
            self._draw_road_map(self.big_map_surface, carla_world, carla_map)
            if not os.path.exists(dirname):
                os.makedirs(dirname)
            pygame.image.save(self.big_map_surface, full_path)
            print(f"Map saved to cache: {full_path}")

        self.surface = self.big_map_surface

    def _draw_road_map(self, map_surface, carla_world, carla_map):
        map_surface.fill((85, 87, 83))
        precision = 0.05

        def lateral_shift(transform, shift):
            transform.rotation.yaw += 90
            return transform.location + shift * transform.get_forward_vector()

        def draw_lane_marking_single_side(surface, waypoints, sign):
            marking_type = carla.LaneMarkingType.NONE
            previous_marking_type = carla.LaneMarkingType.NONE
            markings_list = []
            temp_waypoints = []
            current_lane_marking = carla.LaneMarkingType.NONE
            
            for sample in waypoints:
                lane_marking = sample.left_lane_marking if sign < 0 else sample.right_lane_marking
                if lane_marking is None:
                    continue
                marking_type = lane_marking.type
                if current_lane_marking != marking_type:
                    if temp_waypoints:
                        markings_list.append((previous_marking_type, temp_waypoints))
                    current_lane_marking = marking_type
                    temp_waypoints = temp_waypoints[-1:]
                else:
                    temp_waypoints.append(sample)
                    previous_marking_type = marking_type

            if temp_waypoints:
                markings_list.append((previous_marking_type, temp_waypoints))

            for marking_type, wps in markings_list:
                points = [self.world_to_pixel(lateral_shift(w.transform, sign * w.lane_width * 0.5)) for w in wps]
                if len(points) >= 2:
                    if marking_type == carla.LaneMarkingType.Solid:
                        pygame.draw.lines(surface, COLOR_ROAD_MARKING, False, points, 2)
                    elif marking_type == carla.LaneMarkingType.Broken:
                        for i in range(0, len(points) - 1, 4):
                            if i + 1 < len(points):
                                pygame.draw.line(surface, COLOR_ROAD_MARKING, points[i], points[min(i+2, len(points)-1)], 2)
                    elif marking_type == carla.LaneMarkingType.SolidSolid:
                        pygame.draw.lines(surface, COLOR_ROAD_MARKING, False, points, 4)
                    elif marking_type == carla.LaneMarkingType.SolidBroken or marking_type == carla.LaneMarkingType.BrokenSolid:
                        pygame.draw.lines(surface, COLOR_ROAD_MARKING, False, points, 3)

        # Draw all roads by iterating through all waypoints for better coverage
        print("  Drawing road surfaces...")
        topology = carla_map.get_topology()
        
        # First pass: draw all road surfaces
        for segment in topology:
            waypoint = segment[0]
            waypoints = [waypoint]
            nxt = waypoint.next(precision)
            if len(nxt) > 0:
                nxt = nxt[0]
                while nxt.road_id == waypoint.road_id:
                    waypoints.append(nxt)
                    nxt = nxt.next(precision)
                    if len(nxt) > 0:
                        nxt = nxt[0]
                    else:
                        break

            road_left_side = [lateral_shift(w.transform, -w.lane_width * 0.5) for w in waypoints]
            road_right_side = [lateral_shift(w.transform, w.lane_width * 0.5) for w in waypoints]
            polygon = road_left_side + [x for x in reversed(road_right_side)]
            polygon = [self.world_to_pixel(x) for x in polygon]
            if len(polygon) > 2:
                pygame.draw.polygon(map_surface, COLOR_ROAD, polygon)
        
        # Second pass: draw lane markings
        print("  Drawing lane markings...")
        for segment in topology:
            waypoint = segment[0]
            waypoints = [waypoint]
            nxt = waypoint.next(precision)
            if len(nxt) > 0:
                nxt = nxt[0]
                while nxt.road_id == waypoint.road_id:
                    waypoints.append(nxt)
                    nxt = nxt.next(precision)
                    if len(nxt) > 0:
                        nxt = nxt[0]
                    else:
                        break

            if not waypoint.is_junction:
                draw_lane_marking_single_side(map_surface, waypoints, -1)
                draw_lane_marking_single_side(map_surface, waypoints, 1)
        
        # Draw center lane markings (yellow for opposite direction lanes)
        print("  Drawing center markings...")
        for segment in topology:
            waypoint = segment[0]
            if waypoint.is_junction:
                continue
            waypoints = [waypoint]
            nxt = waypoint.next(precision)
            if len(nxt) > 0:
                nxt = nxt[0]
                while nxt.road_id == waypoint.road_id:
                    waypoints.append(nxt)
                    nxt = nxt.next(precision)
                    if len(nxt) > 0:
                        nxt = nxt[0]
                    else:
                        break
            
            # Draw center line (where opposite traffic would be)
            center_points = [self.world_to_pixel(w.transform.location) for w in waypoints]
            if len(center_points) >= 2:
                pygame.draw.lines(map_surface, pygame.Color(241, 196, 15), False, center_points, 1)
        
        print("  Map generation complete!")

    def world_to_pixel(self, location, offset=(0, 0)):
        x = self.scale * self._pixels_per_meter * (location.x - self._world_offset[0])
        y = self.scale * self._pixels_per_meter * (location.y - self._world_offset[1])
        return [int(x - offset[0]), int(y - offset[1])]

    def world_to_pixel_width(self, width):
        return int(self.scale * self._pixels_per_meter * width)

    def scale_map(self, scale):
        if scale != self.scale:
            self.scale = scale
            width = int(self.big_map_surface.get_width() * self.scale)
            self.surface = pygame.transform.smoothscale(self.big_map_surface, (width, width))


# ==============================================================================
# -- World Manager -------------------------------------------------------------
# ==============================================================================

class WorldManager:
    """Manages CARLA world and rendering"""
    
    def __init__(self, args, event_log):
        self.args = args
        self.event_log = event_log
        self.client = None
        self.world = None
        self.town_map = None
        self.map_image = None
        self.fleet_monitor = FleetMonitor(event_log)
        self.hero_actor = None
        self.traffic_manager = None
        self.npc_vehicles = []
        self.target_vehicle_id = getattr(args, 'vehicle_id', None)  # Vehicle ID to take control of
        self.observe_only = getattr(args, 'observe_only', False)
        
        # Map view
        self.map_offset = [0, 0]
        self.initial_offset = [0, 0]  # Store initial centered position
        self.map_scale = MAP_MIN_SCALE
        self.last_mouse_pos = None
        
        # Display dimensions
        self.map_width = 0
        self.map_height = 0
        
        # Click detection
        self.vehicle_screen_positions = {}  # vehicle_id -> (x, y, radius)
        
        # Current town
        self.current_town = "Town10HD_Opt"
        
    def connect(self, host, port, target_town="Town10HD_Opt"):
        """Connect to CARLA server and load specified town"""
        try:
            self.client = carla.Client(host, port)
            self.client.set_timeout(30.0)  # Longer timeout for map loading
            self.world = self.client.get_world()
            self.town_map = self.world.get_map()
            
            # Get current town name
            self.current_town = self.town_map.name.split('/')[-1]
            
            # # Load target town if not already loaded
            # if self.current_town != target_town:
            #     print(f"Loading {target_town} (currently on {self.current_town})...")
            #     self.world = self.client.load_world(target_town)
            #     self.town_map = self.world.get_map()
            #     self.current_town = target_town
            #     print(f"Map loaded: {target_town}")
            
            # Setup traffic manager with ULTRA-SAFE driving settings
            # self.traffic_manager = self.client.get_trafficmanager(8000)
            # self.traffic_manager.set_global_distance_to_leading_vehicle(15.0)  # Very large distance
            # self.traffic_manager.set_synchronous_mode(False)
            # self.traffic_manager.set_hybrid_physics_mode(False)  # Full physics for all vehicles
            # self.traffic_manager.set_random_device_seed(42)  # Consistent behavior
            
            # Global safety settings - SMOOTH AND SAFE DRIVING
            # self.traffic_manager.global_percentage_speed_difference(40)  # 40% slower than speed limit
            
            # Prevent vehicles from respawning (disappearing and reappearing)
            # self.traffic_manager.set_respawn_dormant_vehicles(False)
            
            self.map_image = MapImage(self.world, self.town_map, PIXELS_PER_METER)
            
            # Find existing vehicle or spawn new hero
            if self.target_vehicle_id is not None:
                # Find existing vehicle by ID
                self._find_existing_vehicle(self.target_vehicle_id)
            elif self.observe_only:
                self._select_existing_vehicle_for_monitoring()
            else:
                # Spawn new hero if no vehicle ID provided
                self._spawn_hero()
            
            if self.hero_actor:
                self.fleet_monitor.set_hero(self.hero_actor, self.town_map)
                # Disable autopilot to take manual control
                if not self.observe_only:
                    try:
                        self.hero_actor.set_autopilot(False)
                        self.event_log.add(f"Took control of vehicle #{self.hero_actor.id}", "INFO")
                    except:
                        pass
            else:
                if self.observe_only:
                    self.event_log.add("No existing vehicle selected; monitoring fleet only", "INFO")
                else:
                    self.event_log.add("No vehicle found or spawned", "WARNING")
                    return False
                
            # Don't spawn NPC traffic if we're taking control of existing vehicle
            # Only spawn if we created a new hero
            if self.target_vehicle_id is None and not self.observe_only:
                # Spawn initial NPC traffic (clear old ones on each run)
                self._spawn_npc_traffic(7)
            
            self.event_log.add("Connected to CARLA server", "INFO")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def _select_existing_vehicle_for_monitoring(self):
        """Select an existing vehicle without changing simulation ownership."""
        vehicles = list(self.world.get_actors().filter('vehicle.*'))
        if not vehicles:
            self.hero_actor = None
            self.event_log.add("No existing vehicles found for monitoring", "WARNING")
            return

        hero_vehicles = [actor for actor in vehicles if actor.attributes.get('role_name') == 'hero']
        self.hero_actor = hero_vehicles[0] if hero_vehicles else vehicles[0]
        self.event_log.add(f"Monitoring existing vehicle #{self.hero_actor.id}", "INFO")
    
    def _cleanup_existing_npcs(self):
        """Destroy existing NPC vehicles from previous runs (keep hero)"""
        destroyed = 0
        for actor in self.world.get_actors().filter('vehicle.*'):
            # Don't destroy hero vehicles
            if actor.attributes.get('role_name') != 'hero':
                try:
                    actor.destroy()
                    destroyed += 1
                except:
                    pass
        if destroyed > 0:
            self.event_log.add(f"Cleaned up {destroyed} old NPC vehicles", "INFO")
            self.world.tick()
    
    def _cleanup_hero(self):
        """Destroy existing hero vehicle for fresh start"""
        for actor in self.world.get_actors().filter('vehicle.*'):
            if actor.attributes.get('role_name') == 'hero':
                try:
                    actor.destroy()
                    self.event_log.add(f"Destroyed old hero #{actor.id}", "INFO")
                except:
                    pass
        self.world.tick()
            
    def _find_existing_vehicle(self, vehicle_id):
        """Find and take control of an existing vehicle by ID"""
        try:
            # Try to find vehicle by ID
            vehicle_id_int = int(vehicle_id)
            
            print(f"[DEBUG] Searching for vehicle with ID: {vehicle_id_int} (type: {type(vehicle_id_int).__name__})")
            
            all_vehicles = list(self.world.get_actors().filter('vehicle.*'))
            print(f"[DEBUG] Found {len(all_vehicles)} vehicles in simulation")
            
            # Try exact match first
            for actor in all_vehicles:
                if actor.id == vehicle_id_int:
                    self.hero_actor = actor
                    # Set role_name to hero for monitoring
                    if hasattr(actor, 'attributes'):
                        actor.attributes['role_name'] = 'hero'
                    self.event_log.add(f"Found existing vehicle: #{actor.id}", "INFO")
                    print(f"[DEBUG] ✓ Found vehicle: ID={actor.id}, Type={actor.type_id}")
                    return
            
            # If not found, try flexible matching (in case of type mismatch)
            print(f"[DEBUG] Exact match failed, trying flexible matching...")
            for actor in all_vehicles:
                try:
                    if int(actor.id) == vehicle_id_int:
                        self.hero_actor = actor
                        if hasattr(actor, 'attributes'):
                            actor.attributes['role_name'] = 'hero'
                        self.event_log.add(f"Found existing vehicle: #{actor.id}", "INFO")
                        print(f"[DEBUG] ✓ Found vehicle (flexible match): ID={actor.id}, Type={actor.type_id}")
                        return
                except (ValueError, TypeError):
                    continue
            
            # List available vehicles for debugging
            print(f"[DEBUG] Available vehicles:")
            for actor in all_vehicles[:10]:
                print(f"  - ID: {actor.id} (type: {type(actor.id).__name__}), Type: {actor.type_id}")
            
            self.event_log.add(f"Vehicle #{vehicle_id} not found in simulation", "WARNING")
            self.hero_actor = None
        except ValueError:
            self.event_log.add(f"Invalid vehicle ID: {vehicle_id}", "WARNING")
            self.hero_actor = None
        except Exception as e:
            self.event_log.add(f"Error finding vehicle: {e}", "WARNING")
            print(f"[DEBUG] Exception while finding vehicle: {e}")
            self.hero_actor = None
    
    def _spawn_hero(self):
        """Spawn hero vehicle (always fresh)"""
        bp_lib = self.world.get_blueprint_library()
        
        # Use Audi TT specifically
        blueprint = None
        for bp in bp_lib.filter('vehicle.*'):
            if 'audi' in bp.id.lower() and 'tt' in bp.id.lower():
                blueprint = bp
                break
        
        # Fallback if Audi TT not found
        if not blueprint:
            all_vehicles = list(bp_lib.filter('vehicle.*'))
            preferred = [bp for bp in all_vehicles 
                        if 'audi' in bp.id.lower()
                        and 'cybertruck' not in bp.id.lower()]
            if preferred:
                blueprint = preferred[0]
            else:
                blueprint = random.choice(all_vehicles)
        
        blueprint.set_attribute('role_name', 'hero')
        
        if blueprint.has_attribute('color'):
            colors = blueprint.get_attribute('color').recommended_values
            if colors:
                blueprint.set_attribute('color', colors[0])
            
        spawn_points = self.town_map.get_spawn_points()
        random.shuffle(spawn_points)
        
        for sp in spawn_points:
            self.hero_actor = self.world.try_spawn_actor(blueprint, sp)
            if self.hero_actor:
                self.event_log.add(f"Spawned hero: #{self.hero_actor.id}", "INFO")
                self.world.tick()
                return
                
        self.event_log.add("Failed to spawn hero vehicle", "WARNING")
        
    def _spawn_npc_traffic(self, count):
        """Spawn NPC vehicles that drive automatically on valid road positions"""
        bp_lib = self.world.get_blueprint_library()
        
        # Use only Audi TT for all NPC vehicles
        audi_tt_bp = None
        for bp in bp_lib.filter('vehicle.*'):
            if 'audi' in bp.id.lower() and 'tt' in bp.id.lower():
                audi_tt_bp = bp
                break
        
        # Fallback to any Audi if TT not found
        if not audi_tt_bp:
            for bp in bp_lib.filter('vehicle.*'):
                if 'audi' in bp.id.lower():
                    audi_tt_bp = bp
                    break
        
        # Final fallback to any car
        if not audi_tt_bp:
            vehicle_bps = [bp for bp in bp_lib.filter('vehicle.*')
                          if 'bus' not in bp.id.lower()
                          and 'truck' not in bp.id.lower()
                          and 'motorcycle' not in bp.id.lower()
                          and 'bike' not in bp.id.lower()]
            audi_tt_bp = vehicle_bps[0] if vehicle_bps else list(bp_lib.filter('vehicle.*'))[0]
        
        spawn_points = self.town_map.get_spawn_points()
        
        # Filter spawn points to only use valid road positions (not in junctions)
        valid_spawn_points = []
        for sp in spawn_points:
            wp = self.town_map.get_waypoint(sp.location, project_to_road=True, lane_type=carla.LaneType.Driving)
            if wp and not wp.is_junction:
                # Check this is a driving lane and spawn point is close to waypoint
                if wp.lane_type == carla.LaneType.Driving:
                    # Verify the spawn point is actually on the road (within 2m of waypoint)
                    dist_to_lane = sp.location.distance(wp.transform.location)
                    if dist_to_lane < 2.0:
                        # Use the waypoint's transform instead of the spawn point's
                        # This ensures we're exactly on the road
                        corrected_sp = wp.transform
                        corrected_sp.location.z = sp.location.z + 0.5  # Slight lift to avoid ground collision
                        valid_spawn_points.append(corrected_sp)
        
        random.shuffle(valid_spawn_points)
        
        # Get existing vehicle positions to avoid collisions
        existing_positions = []
        for actor in self.world.get_actors().filter('vehicle.*'):
            existing_positions.append(actor.get_location())
        
        spawned = 0
        for sp in valid_spawn_points:
            if spawned >= count:
                break
            
            # Check distance from existing vehicles
            too_close = False
            for pos in existing_positions:
                dist = sp.location.distance(pos)
                if dist < 10.0:  # Minimum 10m from other vehicles
                    too_close = True
                    break
            
            if too_close:
                continue
            
            # Clone the blueprint for this spawn (so we can modify color)
            blueprint = audi_tt_bp
            
            if blueprint.has_attribute('color'):
                colors = blueprint.get_attribute('color').recommended_values
                if colors:
                    blueprint.set_attribute('color', random.choice(colors))
                    
            vehicle = self.world.try_spawn_actor(blueprint, sp)
            if vehicle:
                # Wait a tick for physics to settle
                self.world.tick()
                
                if self.traffic_manager is not None:
                    vehicle.set_autopilot(True, self.traffic_manager.get_port())
                else:
                    vehicle.set_autopilot(True)
                
                # Configure traffic manager behavior for SAFE driving
                # Wrap in try-except as some TM functions may not exist in all CARLA versions
                if self.traffic_manager is not None:
                    try:
                        self.traffic_manager.auto_lane_change(vehicle, False)  # NEVER change lanes
                        self.traffic_manager.distance_to_leading_vehicle(vehicle, 15.0)  # Large following distance
                        self.traffic_manager.vehicle_percentage_speed_difference(vehicle, random.uniform(35, 50))  # Slow and steady
                        self.traffic_manager.ignore_lights_percentage(vehicle, 0)  # ALWAYS respect lights
                        self.traffic_manager.ignore_signs_percentage(vehicle, 0)  # ALWAYS respect signs
                        self.traffic_manager.ignore_walkers_percentage(vehicle, 0)  # ALWAYS avoid walkers
                        self.traffic_manager.update_vehicle_lights(vehicle, True)
                    except Exception as e:
                        pass  # Some TM functions may not exist in this CARLA version
                
                self.npc_vehicles.append(vehicle)
                existing_positions.append(sp.location)
                spawned += 1
                
        self.event_log.add(f"Spawned {spawned} NPC vehicles on valid roads", "INFO")
        
    def spawn_more_traffic(self, count=5):
        """Spawn additional NPC traffic"""
        if self.observe_only:
            self.event_log.add("Observer mode: traffic spawning disabled", "WARNING")
            return
        self._spawn_npc_traffic(count)
        
    def tick(self):
        """Update world state"""
        # Log simulation FPS/dt for diagnosing performance issues (to console only)
        snap = self.world.get_snapshot()
        dt = snap.timestamp.delta_seconds
        fps = (1.0 / dt) if dt > 1e-6 else 0.0
        if not hasattr(self, "_last_fps_log") or time.time() - self._last_fps_log > 2.0:
            self._last_fps_log = time.time()
            # Only print to console if FPS drops significantly (below 15)
            if fps < 15:
                print(f"[PERF WARNING] Sim dt={dt:.3f}s (~{fps:.1f} FPS) - Consider reducing camera load")
        
        actors = self.world.get_actors()
        sim_time = snap.timestamp.elapsed_seconds if snap else None
        self.fleet_monitor.update_fleet(actors, self.town_map, sim_time)
        self.fleet_monitor.check_hero_lane()
        
    def render(self, display, display_width, display_height):
        """Render the map view"""
        self.map_width = display_width - PANEL_WIDTH
        self.map_height = display_height
        
        map_surface = pygame.Surface((self.map_width, self.map_height))
        map_surface.fill(COLOR_BG_DARK)
        
        self.map_image.scale_map(self.map_scale)
        
        # Calculate initial centered position
        if self.initial_offset == [0, 0]:
            self.initial_offset[0] = (self.map_width - self.map_image.surface.get_width()) // 2
            self.initial_offset[1] = (self.map_height - self.map_image.surface.get_height()) // 2
            self.map_offset = self.initial_offset.copy()
        
        # If at minimum scale, lock to center
        if self.map_scale <= MAP_MIN_SCALE:
            self.map_offset = self.initial_offset.copy()
        else:
            # Apply panning constraints
            self._apply_pan_constraints()
        
        # Draw map
        map_surface.blit(self.map_image.surface, (self.map_offset[0], self.map_offset[1]))
        
        # Draw direction arrows on roads
        self._render_direction_arrows(map_surface)
        
        # Draw traffic lights
        self._render_traffic_lights(map_surface)
        
        # Draw vehicles
        self._render_vehicles(map_surface)
        
        display.blit(map_surface, (0, 0))
    
    def _render_direction_arrows(self, surface):
        """Render direction arrows on roads to show traffic flow (white road-style chevrons)"""
        if not hasattr(self, '_arrow_cache') or self._arrow_cache_scale != self.map_scale:
            # Generate arrow positions from waypoints (cache to avoid regenerating every frame)
            self._arrow_cache = []
            self._arrow_cache_scale = self.map_scale
            
            # Sample waypoints along the roads
            waypoints = self.town_map.generate_waypoints(35.0)  # Every 35 meters
            
            for wp in waypoints:
                # Skip junctions - too chaotic
                if wp.is_junction:
                    continue
                    
                # Only driving lanes
                if wp.lane_type != carla.LaneType.Driving:
                    continue
                
                # Check curve: compare direction with point ahead
                is_ok = True
                current_yaw = wp.transform.rotation.yaw
                
                # Check if this is a vertical road (yaw near 90 or -90 degrees)
                normalized_yaw = current_yaw
                while normalized_yaw > 180:
                    normalized_yaw -= 360
                while normalized_yaw < -180:
                    normalized_yaw += 360
                is_vertical = abs(abs(normalized_yaw) - 90) < 30  # Within 30 degrees of vertical
                
                # Check point 15m ahead only
                next_wps = wp.next(15.0)
                if next_wps:
                    next_wp = next_wps[0]
                    if next_wp.is_junction:
                        is_ok = False
                    else:
                        yaw_diff = abs(current_yaw - next_wp.transform.rotation.yaw)
                        if yaw_diff > 180:
                            yaw_diff = 360 - yaw_diff
                        if yaw_diff > 20:  # Allow up to 20 degrees
                            is_ok = False
                
                if not is_ok:
                    continue
                
                # Get position and direction
                loc = wp.transform.location
                yaw = wp.transform.rotation.yaw
                
                # Skip vertical arrows entirely (they were showing wrong direction)
                if is_vertical:
                    continue
                
                self._arrow_cache.append((loc, yaw, wp.lane_id))
        
        # Draw white road-style chevron arrows from cache (horizontal roads only)
        arrow_size = max(5, int(8 * self.map_scale))  # Scale with zoom
        line_width = max(1, int(2 * self.map_scale))  # Line thickness scales too
        
        # White color like road markings (semi-transparent)
        arrow_color = pygame.Color(255, 255, 255, 200)
        
        for loc, yaw, lane_id in self._arrow_cache:
            # Convert to screen coordinates
            pos = self.map_image.world_to_pixel(loc)
            screen_x = pos[0] + self.map_offset[0]
            screen_y = pos[1] + self.map_offset[1]
            
            # Skip if off-screen
            if not (-20 <= screen_x <= self.map_width + 20 and -20 <= screen_y <= self.map_height + 20):
                continue
            
            # Calculate arrow direction (CARLA yaw is degrees, 0 = +X, 90 = +Y)
            # In screen coords, Y is inverted
            rad = math.radians(-yaw)  # Negate for screen coordinates
            
            # Draw chevron (V-shape pointing in direction of travel)
            # Chevron tip (front point)
            tip_x = screen_x + arrow_size * math.cos(rad)
            tip_y = screen_y + arrow_size * math.sin(rad)
            
            # Back left point
            back_angle1 = rad + math.radians(150)
            back1_x = screen_x + arrow_size * math.cos(back_angle1)
            back1_y = screen_y + arrow_size * math.sin(back_angle1)
            
            # Back right point
            back_angle2 = rad - math.radians(150)
            back2_x = screen_x + arrow_size * math.cos(back_angle2)
            back2_y = screen_y + arrow_size * math.sin(back_angle2)
            
            # Draw chevron as two lines meeting at the tip (like road markings)
            pygame.draw.line(surface, arrow_color, (back1_x, back1_y), (tip_x, tip_y), line_width)
            pygame.draw.line(surface, arrow_color, (back2_x, back2_y), (tip_x, tip_y), line_width)
        
    def _render_vehicles(self, surface):
        """Render all vehicles as circles with IDs"""
        self.vehicle_screen_positions.clear()
        
        font = pygame.font.SysFont('Arial', 9, bold=True)
        alert_font = pygame.font.SysFont('Arial', 11, bold=True)
        
        # Calculate flash state for alerts
        flash_on = (time.time() * 4) % 2 < 1  # Flash 2 times per second
        
        for vid, vehicle in self.fleet_monitor.vehicles.items():
            if vehicle.location is None:
                continue
                
            center = self.map_image.world_to_pixel(vehicle.location)
            center = (center[0] + self.map_offset[0], center[1] + self.map_offset[1])
            
            if not (0 <= center[0] <= self.map_width and 0 <= center[1] <= self.map_height):
                continue
            
            # Determine radius and color based on vehicle type and state
            if vehicle.is_hero:
                error = self.fleet_monitor.localization_error
                
                if error <= 1.0:
                    # 1m: small circle, certain position
                    radius = max(int(error * PIXELS_PER_METER * self.map_image.scale), 8)
                    
                    if vehicle.status == "ALERT":
                        # Flashing red dot and alert box
                        color = COLOR_STATUS_ALERT if flash_on else pygame.Color(150, 50, 40)
                        
                        # Draw flashing alert box around hero
                        box_size = 60
                        box_rect = pygame.Rect(
                            center[0] - box_size // 2,
                            center[1] - box_size // 2,
                            box_size, box_size
                        )
                        
                        if flash_on:
                            # Draw filled semi-transparent red background
                            box_surf = pygame.Surface((box_size, box_size), pygame.SRCALPHA)
                            box_surf.fill((231, 76, 60, 60))
                            surface.blit(box_surf, (box_rect.x, box_rect.y))
                            # Draw border
                            pygame.draw.rect(surface, COLOR_STATUS_ALERT, box_rect, 3)
                        else:
                            # Just border when not flashing
                            pygame.draw.rect(surface, pygame.Color(150, 50, 40), box_rect, 2)
                        
                        # Draw "STOPPED" label
                        stop_label = alert_font.render("STOPPED", True, COLOR_WHITE)
                        stop_bg = pygame.Surface((stop_label.get_width() + 6, stop_label.get_height() + 4), pygame.SRCALPHA)
                        stop_bg.fill((231, 76, 60, 220) if flash_on else (150, 50, 40, 180))
                        surface.blit(stop_bg, (center[0] - stop_label.get_width() // 2 - 3, center[1] + box_size // 2 + 2))
                        surface.blit(stop_label, (center[0] - stop_label.get_width() // 2, center[1] + box_size // 2 + 4))
                    else:
                        color = COLOR_STATUS_OK
                        
                    # Draw uncertainty circle
                    circle_surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
                    pygame.draw.circle(circle_surf, (*color[:3], 60), (radius, radius), radius)
                    pygame.draw.circle(circle_surf, color, (radius, radius), radius, 2)
                    surface.blit(circle_surf, (center[0] - radius, center[1] - radius))
                    
                    # Draw center dot (flashing for alert)
                    dot_radius = 7 if vehicle.status == "ALERT" else 5
                    pygame.draw.circle(surface, color, center, dot_radius)
                    pygame.draw.circle(surface, COLOR_WHITE, center, dot_radius, 1)
                else:
                    # 5m: large uncertainty circle, unknown exact position
                    # DO NOT draw center dot - we don't know where vehicle is exactly!
                    radius = max(int(error * PIXELS_PER_METER * self.map_image.scale), 20)
                    
                    circle_surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
                    pygame.draw.circle(circle_surf, (*COLOR_STATUS_ALERT[:3], 30), (radius, radius), radius)
                    pygame.draw.circle(circle_surf, COLOR_STATUS_ALERT, (radius, radius), radius, 3)
                    surface.blit(circle_surf, (center[0] - radius, center[1] - radius))
                    
                    # NO center dot or symbol - position is completely uncertain!
            else:
                # NPC vehicles - simple circles
                radius = 5
                
                if vid == self.fleet_monitor.selected_id:
                    color = COLOR_SELECTED
                    radius = 7
                else:
                    color = COLOR_VEHICLE_NPC
                    
                pygame.draw.circle(surface, color, center, radius)
                pygame.draw.circle(surface, COLOR_WHITE, center, radius, 1)
            
            # Store position for click detection
            click_radius = max(radius, 10)
            self.vehicle_screen_positions[vid] = (center[0], center[1], click_radius)
            
            # Draw ID label above vehicle
            label_text = f"#{vid}"
            if vehicle.is_hero:
                label_text = f"#{vid}"
            label = font.render(label_text, True, COLOR_WHITE)
            
            # Background for label
            label_bg = pygame.Surface((label.get_width() + 4, label.get_height() + 2), pygame.SRCALPHA)
            label_bg.fill((0, 0, 0, 150))
            
            label_x = center[0] - label.get_width() // 2
            label_y = center[1] - radius - 14
            
            surface.blit(label_bg, (label_x - 2, label_y - 1))
            surface.blit(label, (label_x, label_y))
    
    def _render_traffic_lights(self, surface):
        """Render traffic lights on the map"""
        # Get all traffic lights from CARLA
        traffic_lights = self.world.get_actors().filter('traffic.traffic_light*')
        
        for tl in traffic_lights:
            world_pos = tl.get_location()
            pos = self.map_image.world_to_pixel(world_pos)
            pos = (pos[0] + self.map_offset[0], pos[1] + self.map_offset[1])
            
            # Skip if off-screen
            if not (0 <= pos[0] <= self.map_width and 0 <= pos[1] <= self.map_height):
                continue
            
            # Draw traffic light based on state
            # Size scales with map zoom
            size = max(8, int(15 * self.map_scale))
            
            # Background (dark rectangle representing the traffic light box)
            bg_w = size
            bg_h = size * 3
            bg_rect = pygame.Rect(pos[0] - bg_w // 2, pos[1] - bg_h // 2, bg_w, bg_h)
            pygame.draw.rect(surface, (46, 52, 54), bg_rect, border_radius=2)
            
            # Determine colors based on state
            state = tl.state
            light_radius = max(3, int(size * 0.35))
            spacing = bg_h // 3
            
            # Default off colors
            red_color = (85, 87, 83)
            yellow_color = (85, 87, 83)
            green_color = (85, 87, 83)
            
            # Active light colors
            if state == carla.TrafficLightState.Red:
                red_color = (239, 41, 41)
            elif state == carla.TrafficLightState.Yellow:
                yellow_color = (252, 233, 79)
            elif state == carla.TrafficLightState.Green:
                green_color = (138, 226, 52)
            
            # Draw the three lights
            center_x = pos[0]
            
            # Red light (top)
            pygame.draw.circle(surface, red_color, (center_x, pos[1] - spacing + light_radius), light_radius)
            
            # Yellow light (middle)
            pygame.draw.circle(surface, yellow_color, (center_x, pos[1]), light_radius)
            
            # Green light (bottom)
            pygame.draw.circle(surface, green_color, (center_x, pos[1] + spacing - light_radius), light_radius)
                
    def handle_scroll(self, amount, mouse_pos):
        """Handle zoom - only allow zoom IN"""
        old_scale = self.map_scale
        new_scale = self.map_scale + amount * 0.05
        
        # Only allow zoom in (not out past initial scale)
        self.map_scale = max(MAP_MIN_SCALE, min(MAP_MAX_SCALE, new_scale))
        
        if old_scale != self.map_scale:
            mx, my = mouse_pos
            
            # Zoom toward mouse position
            map_x = (mx - self.map_offset[0]) / old_scale
            map_y = (my - self.map_offset[1]) / old_scale
            
            self.map_offset[0] = mx - map_x * self.map_scale
            self.map_offset[1] = my - map_y * self.map_scale
            
            # Apply constraints after zoom
            self._apply_pan_constraints()
        
    def _apply_pan_constraints(self):
        """Apply panning constraints to keep map visible"""
        if self.map_image is None or self.map_width == 0:
            return
            
        scaled_width = self.map_image.surface.get_width()
        scaled_height = self.map_image.surface.get_height()
        
        # If map is larger than view, keep edges from showing
        if scaled_width > self.map_width:
            # offset can be from (map_width - scaled_width) to 0
            # e.g., scaled=1500, view=1000 -> offset from -500 to 0
            self.map_offset[0] = max(self.map_width - scaled_width, min(0, self.map_offset[0]))
        else:
            # Map smaller than view - center it
            self.map_offset[0] = (self.map_width - scaled_width) // 2
            
        if scaled_height > self.map_height:
            self.map_offset[1] = max(self.map_height - scaled_height, min(0, self.map_offset[1]))
        else:
            self.map_offset[1] = (self.map_height - scaled_height) // 2
            
    def handle_drag(self, mouse_pos, pressed):
        """Handle map panning - only when zoomed in"""
        # Don't allow panning at minimum scale
        if self.map_scale <= MAP_MIN_SCALE:
            self.last_mouse_pos = None
            return
            
        if pressed:
            if self.last_mouse_pos:
                dx = mouse_pos[0] - self.last_mouse_pos[0]
                dy = mouse_pos[1] - self.last_mouse_pos[1]
                self.map_offset[0] += dx
                self.map_offset[1] += dy
                # Apply constraints immediately
                self._apply_pan_constraints()
            self.last_mouse_pos = mouse_pos
        else:
            self.last_mouse_pos = None
            
    def handle_map_click(self, pos):
        """Handle click on map to select vehicle"""
        for vid, (x, y, radius) in self.vehicle_screen_positions.items():
            dist = math.sqrt((pos[0] - x)**2 + (pos[1] - y)**2)
            if dist <= radius + 5:  # Small tolerance
                self.fleet_monitor.select_vehicle(vid)
                return True
        return False
        
    def destroy(self):
        """Cleanup"""
        if self.observe_only:
            return

        # Destroy NPC vehicles
        for vehicle in self.npc_vehicles:
            try:
                vehicle.destroy()
            except:
                pass
        self.npc_vehicles.clear()
        
        # Don't destroy hero - let user keep it


# ==============================================================================
# -- Main Application ----------------------------------------------------------
# ==============================================================================

class TeleoperatorApp:
    """Main application"""
    
    TELEOP_LOCK_FILE = "/tmp/carla_teleop_active.lock"
    
    def __init__(self, args):
        self.args = args
        self.running = True
        
        pygame.init()
        self.display = pygame.display.set_mode((args.width, args.height), pygame.HWSURFACE | pygame.DOUBLEBUF)
        pygame.display.set_caption("Teleoperator Monitor")
        self.clock = pygame.time.Clock()
        
        self.event_log = EventLog()
        self.world_manager = WorldManager(args, self.event_log)
        self.control_panel = ControlPanel(args.width, args.height)
        
        self.autopilot = False
        self.control = None
        self.steer_cache = 0.0
        
    def is_teleop_active(self):
        """Check if teleop window has control of the hero vehicle"""
        import os
        return os.path.exists(self.TELEOP_LOCK_FILE)
        
    def run(self):
        """Main loop"""
        print("Connecting to CARLA...")
        if not self.world_manager.connect(self.args.host, self.args.port):
            print("Failed to connect!")
            return
        
        # Pass current town to control panel
        self.control_panel.set_current_town(self.world_manager.current_town)
            
        print("\n" + "="*50)
        print("  V2X FLEET TELEOPERATOR MONITOR")
        print("="*50)
        print(f"\n  Map: {self.world_manager.current_town}")
        print("\n  1 = 1m accuracy    5 = 5m accuracy")
        print("  R = Reset lane     C = Clear alert")
        print("  T = Spawn more traffic")
        print("  V = Open 3D camera view")
        print("  WASD = Drive       P = Autopilot")
        print("  Mouse wheel = Zoom in")
        print("  Click vehicle = Select for monitoring")
        print("="*50 + "\n")
        
        self.control = carla.VehicleControl()
        
        while self.running:
            self.clock.tick(60)
            self._handle_events()
            self.world_manager.tick()
            
            self.display.fill(COLOR_BG_DARK)
            self.world_manager.render(self.display, self.args.width, self.args.height)
            self.control_panel.render(self.display, self.world_manager.fleet_monitor, self.event_log)
            
            pygame.display.flip()
            
        self.world_manager.destroy()
        pygame.quit()
        
    def _handle_events(self):
        """Handle input events"""
        mouse_pos = pygame.mouse.get_pos()
        mouse_pressed = pygame.mouse.get_pressed()[0]
        
        # Handle map dragging (only in map area and when zoomed in)
        if mouse_pos[0] < self.args.width - PANEL_WIDTH:
            self.world_manager.handle_drag(mouse_pos, mouse_pressed)
        else:
            self.world_manager.handle_drag(mouse_pos, False)
        
        self.control_panel.update_hover(mouse_pos)
        
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                
            elif event.type == pygame.KEYDOWN:
                if event.key == K_ESCAPE:
                    self.running = False
                elif event.key == K_1:
                    self.world_manager.fleet_monitor.set_localization(1.0)
                elif event.key == K_5:
                    self.world_manager.fleet_monitor.set_localization(5.0)
                elif event.key == K_r:
                    self.world_manager.fleet_monitor.reset_hero_lane(self.world_manager.town_map)
                elif event.key == K_c:
                    # Take Control - open teleop station for hero (only if vehicle was selected from VehicleMap)
                    if self.world_manager.hero_actor:
                        if self.world_manager.fleet_monitor.alert_active:
                            self.world_manager.fleet_monitor.clear_alert()
                            self.event_log.add("Alert cleared - taking control", "INFO")
                        # Only launch teleop for the vehicle that was selected (hero_actor.id matches the vehicle_id passed)
                        self.control_panel._launch_teleop_station(self.world_manager.hero_actor.id)
                        self.event_log.add(f"Teleop opened for vehicle #{self.world_manager.hero_actor.id}", "INFO")
                elif event.key == K_d:
                    # Dismiss/Seen - clear the alert with cooldown timer
                    if self.world_manager.fleet_monitor.alert_active:
                        self.world_manager.fleet_monitor.dismiss_alert()
                        self.event_log.add("Alert dismissed (5s cooldown)", "INFO")
                elif event.key == K_t:
                    self.world_manager.spawn_more_traffic(5)
                elif event.key == K_p:
                    if self.world_manager.observe_only:
                        self.event_log.add("Observer mode: autopilot changes disabled", "WARNING")
                        continue
                    self.autopilot = not self.autopilot
                    if self.world_manager.hero_actor:
                        self.world_manager.hero_actor.set_autopilot(self.autopilot)
                    self.event_log.add(f"Autopilot: {'ON' if self.autopilot else 'OFF'}", "INFO")
                elif event.key == K_v:
                    # Launch 3D view for hero
                    if self.world_manager.hero_actor:
                        self.control_panel._launch_3d_view(self.world_manager.hero_actor.id)
                        self.event_log.add("Launched 3D camera view", "INFO")
                    
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:  # Left click
                    # First check popup button (highest priority when alert is active)
                    if self.world_manager.fleet_monitor.alert_active:
                        if self.control_panel.handle_popup_click(mouse_pos, self.world_manager.fleet_monitor, self.event_log):
                            continue
                    
                    if mouse_pos[0] < self.args.width - PANEL_WIDTH:
                        # Click on map - try to select vehicle
                        self.world_manager.handle_map_click(mouse_pos)
                    else:
                        # Click on panel
                        self.control_panel.handle_click(mouse_pos, self.world_manager.fleet_monitor, self.event_log)
                        
                elif event.button == 4:  # Scroll up = zoom in
                    if mouse_pos[0] < self.args.width - PANEL_WIDTH:
                        self.world_manager.handle_scroll(1, mouse_pos)
                    else:
                        self.control_panel.handle_scroll(mouse_pos, 1, self.world_manager.fleet_monitor)
                        
                elif event.button == 5:  # Scroll down = zoom out
                    if mouse_pos[0] < self.args.width - PANEL_WIDTH:
                        self.world_manager.handle_scroll(-1, mouse_pos)
                    else:
                        self.control_panel.handle_scroll(mouse_pos, -1, self.world_manager.fleet_monitor)
                    
        # Continuous key handling for driving
        if self.world_manager.hero_actor:
            if self.world_manager.observe_only:
                return

            # Skip control if teleop window has ownership
            if self.is_teleop_active():
                return  # Let teleop handle all control
            
            # Check if hero is stopped due to alert
            if self.world_manager.fleet_monitor.hero_stopped:
                # Force stop the vehicle - apply full brake
                self.control.throttle = 0.0
                self.control.brake = 1.0
                self.control.hand_brake = True
                self.control.steer = 0.0
                self.world_manager.hero_actor.apply_control(self.control)
                
                # Disable autopilot if it was on
                if self.autopilot:
                    self.autopilot = False
                    self.world_manager.hero_actor.set_autopilot(False)
            elif not self.autopilot:
                keys = pygame.key.get_pressed()
                
                self.control.throttle = 1.0 if keys[K_w] or keys[K_UP] else 0.0
                self.control.brake = 1.0 if keys[K_s] or keys[K_DOWN] else 0.0
                self.control.hand_brake = keys[K_SPACE]
                
                steer_increment = 0.03
                if keys[K_a] or keys[K_LEFT]:
                    self.steer_cache = max(-0.7, self.steer_cache - steer_increment)
                elif keys[K_d] or keys[K_RIGHT]:
                    self.steer_cache = min(0.7, self.steer_cache + steer_increment)
                else:
                    self.steer_cache = 0.0
                    
                self.control.steer = self.steer_cache
                self.world_manager.hero_actor.apply_control(self.control)


# ==============================================================================
# -- Main ----------------------------------------------------------------------
# ==============================================================================

def main():
    argparser = argparse.ArgumentParser(description='Teleoperator Monitor')
    argparser.add_argument('--host', default='127.0.0.1', help='CARLA host')
    argparser.add_argument('-p', '--port', default=2000, type=int, help='CARLA port')
    argparser.add_argument('--res', default='1920x1080', help='Window resolution')
    argparser.add_argument('--clear-cache', action='store_true', help='Clear map cache and regenerate')
    argparser.add_argument('--vehicle-id', type=int, default=None, help='Vehicle ID to take control of (if not provided, spawns new hero)')
    argparser.add_argument('--observe-only', action='store_true', help='Monitor existing CARLA actors without spawning, destroying, or controlling vehicles')
    
    args = argparser.parse_args()
    args.width, args.height = [int(x) for x in args.res.split('x')]
    
    # Clear cache if requested
    if args.clear_cache:
        cache_dir = os.path.join("cache", "no_rendering_mode")
        if os.path.exists(cache_dir):
            import shutil
            shutil.rmtree(cache_dir)
            print(f"Cleared map cache: {cache_dir}")
    
    app = TeleoperatorApp(args)
    app.run()


if __name__ == '__main__':
    main()
