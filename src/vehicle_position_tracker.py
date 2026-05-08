#!/usr/bin/env python3

"""
Vehicle Position Tracker - Debug Tool
Tracks and prints vehicle positions for visualization on a map
"""

import json
import time
from collections import defaultdict
from datetime import datetime


class VehiclePositionTracker:
    """
    Tracks vehicle positions and outputs them in a format suitable for map visualization
    """

    def __init__(self, tracked_vehicle_ids=None, output_file=None, verbose=True):
        """
        Initialize the position tracker

        Args:
            tracked_vehicle_ids: List of SUMO vehicle IDs to track (None = track all)
            output_file: Optional file path to write position data (JSON format)
            verbose: Print position updates to console (default: True)
        """
        self.tracked_vehicle_ids = set(tracked_vehicle_ids) if tracked_vehicle_ids else None
        self.output_file = output_file
        self.positions = defaultdict(list)  # vehicle_id -> list of positions
        self.start_time = time.time()
        self.verbose = verbose

    def track_position(self, vehicle_id, carla_x, carla_y, carla_z=0,
                      gps_lon=None, gps_lat=None, speed=None, heading=None,
                      additional_data=None):
        """
        Track a vehicle position

        Args:
            vehicle_id: SUMO vehicle ID
            carla_x, carla_y, carla_z: Carla coordinates
            gps_lon, gps_lat: GPS coordinates (optional)
            speed: Vehicle speed in m/s (optional)
            heading: Vehicle heading in degrees (optional)
            additional_data: Dictionary of additional data to track (optional)
        """
        # Skip if we're only tracking specific vehicles and this isn't one of them
        if self.tracked_vehicle_ids and vehicle_id not in self.tracked_vehicle_ids:
            return

        timestamp = time.time() - self.start_time

        position_data = {
            'timestamp': timestamp,
            'vehicle_id': vehicle_id,
            'carla': {'x': carla_x, 'y': carla_y, 'z': carla_z}
        }

        if gps_lon is not None and gps_lat is not None:
            position_data['gps'] = {'lon': gps_lon, 'lat': gps_lat}

        if speed is not None:
            position_data['speed'] = speed

        if heading is not None:
            position_data['heading'] = heading

        if additional_data:
            position_data.update(additional_data)

        self.positions[vehicle_id].append(position_data)

        # Print debug output only if verbose
        if self.verbose:
            self._print_position(position_data)

    def _print_position(self, position_data):
        """Print position data in a readable format"""
        vehicle_id = position_data['vehicle_id']
        carla = position_data['carla']
        timestamp = position_data['timestamp']

        output = f"[POSITION {timestamp:.2f}s] Vehicle: {vehicle_id}"
        output += f" | Carla: ({carla['x']:.2f}, {carla['y']:.2f}, {carla['z']:.2f})"

        if 'gps' in position_data:
            gps = position_data['gps']
            output += f" | GPS: ({gps['lon']:.6f}, {gps['lat']:.6f})"

        if 'speed' in position_data:
            output += f" | Speed: {position_data['speed']:.2f} m/s"

        if 'heading' in position_data:
            output += f" | Heading: {position_data['heading']:.2f}°"

        print(output)

    def get_trajectory(self, vehicle_id):
        """Get the complete trajectory for a vehicle"""
        return self.positions.get(vehicle_id, [])

    def get_all_trajectories(self):
        """Get trajectories for all tracked vehicles"""
        return dict(self.positions)

    def save_to_file(self, filename=None):
        """Save all position data to a JSON file for map visualization"""
        output_file = filename or self.output_file

        if not output_file:
            output_file = f"vehicle_positions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        data = {
            'start_time': self.start_time,
            'vehicles': {}
        }

        for vehicle_id, trajectory in self.positions.items():
            data['vehicles'][vehicle_id] = {
                'num_positions': len(trajectory),
                'trajectory': trajectory
            }

        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"\n[TRACKER] Saved {len(self.positions)} vehicle trajectories to: {output_file}")
        return output_file

    def print_summary(self):
        """Print a summary of tracked vehicles"""
        print("\n" + "="*60)
        print("Vehicle Position Tracker Summary")
        print("="*60)
        print(f"Total vehicles tracked: {len(self.positions)}")
        print(f"Simulation time: {time.time() - self.start_time:.2f}s")
        print("\nVehicle statistics:")

        for vehicle_id, trajectory in sorted(self.positions.items()):
            if trajectory:
                first_pos = trajectory[0]['carla']
                last_pos = trajectory[-1]['carla']

                distance = ((last_pos['x'] - first_pos['x'])**2 +
                          (last_pos['y'] - first_pos['y'])**2)**0.5

                print(f"  {vehicle_id}:")
                print(f"    - Positions recorded: {len(trajectory)}")
                print(f"    - Distance traveled: {distance:.2f}m")

                if 'speed' in trajectory[-1]:
                    avg_speed = sum(p.get('speed', 0) for p in trajectory) / len(trajectory)
                    print(f"    - Average speed: {avg_speed:.2f} m/s")

        print("="*60 + "\n")

    def clear(self):
        """Clear all tracked data"""
        self.positions.clear()
        self.start_time = time.time()
        print("[TRACKER] Position data cleared")


def create_leaflet_html(json_file, output_html=None):
    """
    Create a simple Leaflet map HTML file to visualize the vehicle trajectories

    Args:
        json_file: Path to the JSON file with position data
        output_html: Output HTML file path (optional)
    """
    import json

    with open(json_file, 'r') as f:
        data = json.load(f)

    if output_html is None:
        output_html = json_file.replace('.json', '.html')

    # Generate random colors for each vehicle
    colors = ['red', 'blue', 'green', 'purple', 'orange', 'darkred',
             'darkblue', 'darkgreen', 'cadetblue', 'darkpurple']

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Vehicle Position Tracker</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
    <style>
        #map {{ height: 100vh; width: 100%; }}
        body {{ margin: 0; padding: 0; }}
        .info {{
            padding: 6px 8px;
            background: white;
            background: rgba(255,255,255,0.8);
            box-shadow: 0 0 15px rgba(0,0,0,0.2);
            border-radius: 5px;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var data = {json.dumps(data)};

        // Initialize the map (centered on first position if GPS data available)
        var map = L.map('map');

        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '&copy; OpenStreetMap contributors'
        }}).addTo(map);

        var bounds = L.latLngBounds();
        var colors = {json.dumps(colors)};
        var vehicleIndex = 0;

        // Add trajectories for each vehicle
        for (var vehicleId in data.vehicles) {{
            var vehicle = data.vehicles[vehicleId];
            var trajectory = vehicle.trajectory;
            var color = colors[vehicleIndex % colors.length];
            vehicleIndex++;

            // Check if we have GPS data
            var hasGPS = trajectory.length > 0 && trajectory[0].gps;

            if (hasGPS) {{
                var latLngs = trajectory.map(p => [p.gps.lat, p.gps.lon]);

                // Draw the trajectory
                var polyline = L.polyline(latLngs, {{
                    color: color,
                    weight: 3,
                    opacity: 0.7
                }}).addTo(map);

                // Add markers for start and end
                L.circleMarker(latLngs[0], {{
                    radius: 8,
                    fillColor: color,
                    color: 'white',
                    weight: 2,
                    opacity: 1,
                    fillOpacity: 0.8
                }}).bindPopup(`<b>${{vehicleId}}</b><br>Start`).addTo(map);

                L.circleMarker(latLngs[latLngs.length-1], {{
                    radius: 8,
                    fillColor: color,
                    color: 'black',
                    weight: 2,
                    opacity: 1,
                    fillOpacity: 0.8
                }}).bindPopup(`<b>${{vehicleId}}</b><br>End`).addTo(map);

                bounds.extend(latLngs);
            }}
        }}

        // Fit map to show all trajectories
        if (bounds.isValid()) {{
            map.fitBounds(bounds);
        }} else {{
            map.setView([0, 0], 13);
        }}

        // Add legend
        var legend = L.control({{position: 'topright'}});
        legend.onAdd = function(map) {{
            var div = L.DomUtil.create('div', 'info');
            div.innerHTML = '<h4>Vehicles</h4>';
            var idx = 0;
            for (var vehicleId in data.vehicles) {{
                var color = colors[idx % colors.length];
                div.innerHTML += `<i style="background:${{color}};width:18px;height:18px;float:left;margin-right:8px;opacity:0.7;"></i>${{vehicleId}}<br>`;
                idx++;
            }}
            return div;
        }};
        legend.addTo(map);
    </script>
</body>
</html>"""

    with open(output_html, 'w') as f:
        f.write(html_content)

    print(f"[TRACKER] Created map visualization: {output_html}")
    print(f"[TRACKER] Open in browser: file://{output_html}")
    return output_html


if __name__ == '__main__':
    # Example usage
    tracker = VehiclePositionTracker(tracked_vehicle_ids=['vehicle_0', 'vehicle_1'])

    # Simulate some positions
    for i in range(10):
        tracker.track_position(
            vehicle_id='vehicle_0',
            carla_x=100 + i*10,
            carla_y=200 + i*5,
            gps_lon=11.567 + i*0.0001,
            gps_lat=48.148 + i*0.0001,
            speed=13.5,
            heading=180.0
        )

    tracker.print_summary()
    json_file = tracker.save_to_file()
    create_leaflet_html(json_file)
