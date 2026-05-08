#!/usr/bin/env python3

"""
Vehicle Map Plotter - Real-time visualization tool
Plots vehicle positions on a map with their IDs
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.animation import FuncAnimation
from collections import defaultdict
import numpy as np
import threading
import time


class VehicleMapPlotter:
    """
    Real-time map plotter for vehicle positions
    """

    def __init__(self, update_interval=1.0, use_gps=False, max_history=50):
        """
        Initialize the map plotter

        Args:
            update_interval: How often to update the plot (seconds)
            use_gps: Use GPS coordinates instead of Carla coordinates
            max_history: Maximum number of historical positions to show per vehicle
        """
        self.update_interval = update_interval
        self.use_gps = use_gps
        self.max_history = max_history

        self.vehicles = defaultdict(lambda: {
            'positions': [],
            'timestamps': [],
            'color': None
        })

        self.fig = None
        self.ax = None
        self.running = False
        self.lock = threading.Lock()

        # Color palette for vehicles
        self.colors = plt.cm.tab20(np.linspace(0, 1, 20))
        self.color_index = 0

    def start_plotting(self, blocking=False):
        """Start the real-time plotting"""
        if self.running:
            return

        self.running = True

        # Use non-interactive backend for thread safety
        import matplotlib
        matplotlib.use('Agg')  # Use non-GUI backend

        self.fig, self.ax = plt.subplots(figsize=(12, 10))

        if blocking:
            # Blocking mode - update in the main thread
            self._setup_plot()
            plt.show()
        else:
            # Non-blocking mode - just setup, updates happen on demand
            self._setup_plot()
            print("[MAP] Map plotter initialized (non-interactive mode)")

    def _setup_plot(self):
        """Setup the plot axes and labels"""
        self.ax.set_xlabel('X (Carla)' if not self.use_gps else 'Longitude')
        self.ax.set_ylabel('Y (Carla)' if not self.use_gps else 'Latitude')
        self.ax.set_title('Vehicle Positions')
        self.ax.grid(True, alpha=0.3)

    def update_if_needed(self):
        """Update plot if enough time has passed since last update"""
        if not self.running:
            return

        current_time = time.time()
        if not hasattr(self, 'last_update_time'):
            self.last_update_time = 0

        if current_time - self.last_update_time >= self.update_interval:
            self._update_plot()
            self.last_update_time = current_time

    def _update_plot(self):
        """Update the plot with current vehicle positions"""
        with self.lock:
            self.ax.clear()
            self._setup_plot()

            if not self.vehicles:
                return

            # Plot each vehicle
            for vehicle_id, data in self.vehicles.items():
                if not data['positions']:
                    continue

                positions = np.array(data['positions'])

                # Plot trajectory (historical positions)
                if len(positions) > 1:
                    self.ax.plot(positions[:, 0], positions[:, 1],
                               alpha=0.3, color=data['color'], linewidth=1)

                # Plot current position with larger marker
                current_pos = positions[-1]
                self.ax.scatter(current_pos[0], current_pos[1],
                              s=200, color=data['color'],
                              edgecolors='black', linewidths=2, zorder=5)

                # Add vehicle ID label
                self.ax.annotate(vehicle_id,
                               xy=(current_pos[0], current_pos[1]),
                               xytext=(5, 5), textcoords='offset points',
                               fontsize=9, fontweight='bold',
                               bbox=dict(boxstyle='round,pad=0.3',
                                       facecolor=data['color'],
                                       edgecolor='black', alpha=0.7))

            # Auto-scale to fit all vehicles
            self.ax.relim()
            self.ax.autoscale_view()

            # Add legend with vehicle count
            legend_text = f"Vehicles: {len(self.vehicles)}"
            self.ax.text(0.02, 0.98, legend_text,
                       transform=self.ax.transAxes,
                       fontsize=12, verticalalignment='top',
                       bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    def update_vehicle_position(self, vehicle_id, x, y, timestamp=None):
        """
        Update a vehicle's position

        Args:
            vehicle_id: Vehicle identifier
            x: X coordinate (Carla) or Longitude (GPS)
            y: Y coordinate (Carla) or Latitude (GPS)
            timestamp: Optional timestamp
        """
        with self.lock:
            vehicle_data = self.vehicles[vehicle_id]

            # Assign color if new vehicle
            if vehicle_data['color'] is None:
                vehicle_data['color'] = self.colors[self.color_index % len(self.colors)]
                self.color_index += 1

            # Add position
            vehicle_data['positions'].append([x, y])
            if timestamp is not None:
                vehicle_data['timestamps'].append(timestamp)

            # Limit history
            if len(vehicle_data['positions']) > self.max_history:
                vehicle_data['positions'].pop(0)
                if vehicle_data['timestamps']:
                    vehicle_data['timestamps'].pop(0)

    def save_current_plot(self, filename='vehicle_map.png'):
        """Save the current plot to a file"""
        with self.lock:
            self._update_plot()
            self.fig.savefig(filename, dpi=150, bbox_inches='tight')
            print(f"[MAP] Saved plot to: {filename}")

    def stop_plotting(self):
        """Stop the plotting"""
        self.running = False
        if self.fig:
            plt.close(self.fig)

    def clear_vehicles(self):
        """Clear all vehicle data"""
        with self.lock:
            self.vehicles.clear()
            self.color_index = 0


class VehicleMapPlotterInteractive:
    """
    Interactive map plotter using matplotlib's animation
    """

    def __init__(self, update_interval=100, use_gps=False, max_history=50):
        """
        Initialize the interactive plotter

        Args:
            update_interval: Update interval in milliseconds
            use_gps: Use GPS coordinates instead of Carla coordinates
            max_history: Maximum number of historical positions to show per vehicle
        """
        self.update_interval = update_interval
        self.use_gps = use_gps
        self.max_history = max_history

        self.vehicles = defaultdict(lambda: {
            'positions': [],
            'timestamps': [],
            'color': None,
            'scatter': None,
            'line': None,
            'annotation': None
        })

        self.lock = threading.Lock()
        self.colors = plt.cm.tab20(np.linspace(0, 1, 20))
        self.color_index = 0

        # Setup figure
        self.fig, self.ax = plt.subplots(figsize=(14, 10))
        self.ax.set_xlabel('X (Carla)' if not self.use_gps else 'Longitude')
        self.ax.set_ylabel('Y (Carla)' if not self.use_gps else 'Latitude')
        self.ax.set_title('Real-time Vehicle Positions')
        self.ax.grid(True, alpha=0.3)

        # Animation
        self.anim = None

    def update_vehicle_position(self, vehicle_id, x, y, timestamp=None):
        """Update a vehicle's position"""
        with self.lock:
            vehicle_data = self.vehicles[vehicle_id]

            # Assign color if new vehicle
            if vehicle_data['color'] is None:
                vehicle_data['color'] = self.colors[self.color_index % len(self.colors)]
                self.color_index += 1

            # Add position
            vehicle_data['positions'].append([x, y])
            if timestamp is not None:
                vehicle_data['timestamps'].append(timestamp)

            # Limit history
            if len(vehicle_data['positions']) > self.max_history:
                vehicle_data['positions'].pop(0)
                if vehicle_data['timestamps']:
                    vehicle_data['timestamps'].pop(0)

    def _animate(self, frame):
        """Animation update function"""
        with self.lock:
            # Clear previous artists
            self.ax.clear()
            self.ax.set_xlabel('X (Carla)' if not self.use_gps else 'Longitude')
            self.ax.set_ylabel('Y (Carla)' if not self.use_gps else 'Latitude')
            self.ax.set_title(f'Real-time Vehicle Positions (Frame {frame})')
            self.ax.grid(True, alpha=0.3)

            if not self.vehicles:
                return

            # Plot each vehicle
            for vehicle_id, data in self.vehicles.items():
                if not data['positions']:
                    continue

                positions = np.array(data['positions'])

                # Plot trajectory
                if len(positions) > 1:
                    self.ax.plot(positions[:, 0], positions[:, 1],
                               alpha=0.4, color=data['color'], linewidth=1.5)

                # Plot current position
                current_pos = positions[-1]
                self.ax.scatter(current_pos[0], current_pos[1],
                              s=250, color=data['color'],
                              edgecolors='black', linewidths=2.5, zorder=5,
                              marker='o')

                # Add vehicle ID label
                self.ax.annotate(vehicle_id,
                               xy=(current_pos[0], current_pos[1]),
                               xytext=(8, 8), textcoords='offset points',
                               fontsize=10, fontweight='bold',
                               bbox=dict(boxstyle='round,pad=0.4',
                                       facecolor=data['color'],
                                       edgecolor='black', alpha=0.8))

            # Auto-scale
            self.ax.relim()
            self.ax.autoscale_view()

            # Add info text
            info_text = f"Vehicles: {len(self.vehicles)}\nFrame: {frame}"
            self.ax.text(0.02, 0.98, info_text,
                       transform=self.ax.transAxes,
                       fontsize=11, verticalalignment='top',
                       bbox=dict(boxstyle='round', facecolor='white', alpha=0.9))

    def start_animation(self):
        """Start the animation"""
        self.anim = FuncAnimation(self.fig, self._animate,
                                 interval=self.update_interval,
                                 blit=False, cache_frame_data=False)
        plt.show()

    def save_animation(self, filename='vehicle_animation.gif', duration=10):
        """Save animation to file"""
        # This would require additional setup with writers
        print(f"[MAP] Animation save not implemented yet")


if __name__ == '__main__':
    # Example usage
    plotter = VehicleMapPlotter(update_interval=0.5, use_gps=False)

    # Start plotting in non-blocking mode
    plotter.start_plotting(blocking=False)

    # Simulate some vehicle movements
    for i in range(100):
        plotter.update_vehicle_position('vehicle_0', 100 + i, 200 + i*0.5, i*0.05)
        plotter.update_vehicle_position('vehicle_1', 150 + i*0.8, 180 + i*0.3, i*0.05)
        plotter.update_vehicle_position('vehicle_2', 120 + i*0.6, 220 + i*0.7, i*0.05)

        # Update plot if enough time has passed
        plotter.update_if_needed()
        time.sleep(0.05)

    # Save final plot
    plotter.save_current_plot('final_positions.png')
    plotter.stop_plotting()

    print("Example complete. Check final_positions.png")
