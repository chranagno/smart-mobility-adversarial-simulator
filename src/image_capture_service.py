#!/usr/bin/env python3

"""
Carla Image Capture Service
HTTP server that captures images from Carla simulator
"""

import sys
import os
import glob
import time
import queue
import io
import math
from threading import Lock
from flask import Flask, jsonify, send_file, request, Response
from flask_cors import CORS

# Add Carla Python API to path
try:
    sys.path.append(
        glob.glob(f'{os.environ.get("CARLA_HOME", "/opt/carla-simulator")}/PythonAPI/carla/dist/carla-*%d.%d-%s.egg' %
                  (sys.version_info.major, sys.version_info.minor,
                   'win-amd64' if os.name == 'nt' else 'linux-x86_64'))[0])
except IndexError:
    print("Error: Could not find CARLA Python API. Please set CARLA_HOME environment variable.")
    sys.exit(1)

import carla
import numpy as np

app = Flask(__name__)
CORS(app)

# Global state
carla_client = None
carla_world = None

camera_sensor=None

spectator_camera = None
vehicle_cameras = {}  # vehicle_id → camera actor
latest_spectator_image = None
latest_vehicle_images = {}  # vehicle_id → last frame

image_lock = Lock()
image_queue = queue.Queue(maxsize=2)


def connect_to_carla(host='localhost', port=2000, timeout=10.0):
    """Connect to Carla server"""
    global carla_client, carla_world

    try:
        print(f"Connecting to Carla at {host}:{port}...")
        carla_client = carla.Client(host, port)
        carla_client.set_timeout(timeout)
        carla_world = carla_client.get_world()
        print("Connected to Carla successfully!")
        return True
    except Exception as e:
        print(f"Failed to connect to Carla: {e}")
        return False


# def image_callback(image):
#     """Callback when camera sensor receives a frame"""
#     global latest_image

#     # Convert carla.Image to numpy array
#     array = np.frombuffer(image.raw_data, dtype=np.dtype("uint8"))
#     array = np.reshape(array, (image.height, image.width, 4))  # BGRA
#     array = array[:, :, :3]  # Remove alpha channel
#     array = array[:, :, ::-1]  # Convert BGR to RGB

#     with image_lock:
#         latest_image = array

#     # Also add to queue for streaming
#     try:
#         image_queue.put_nowait(array)
#     except queue.Full:
#         try:
#             image_queue.get_nowait()
#             image_queue.put_nowait(array)
#         except:
#             pass



def spectator_callback(image):
    global latest_spectator_image
    array = np.frombuffer(image.raw_data, dtype=np.uint8)
    array = array.reshape((image.height, image.width, 4))[:, :, :3][:, :, ::-1]
    with image_lock:
        latest_spectator_image = array


def vehicle_callback_factory(vehicle_id):
    def callback(image):
        array = np.frombuffer(image.raw_data, dtype=np.uint8)
        array = array.reshape((image.height, image.width, 4))[:, :, :3][:, :, ::-1]
        with image_lock:
            latest_vehicle_images[vehicle_id] = array
    return callback


def create_vehicle_camera(vehicle_id, width=800, height=600, fov=90):
    global vehicle_cameras, latest_vehicle_images

    # Destroy existing camera for that vehicle
    if vehicle_id in vehicle_cameras:
        try:
            vehicle_cameras[vehicle_id].destroy()
        except:
            pass
        del vehicle_cameras[vehicle_id]

    # Reset image
    latest_vehicle_images[vehicle_id] = None

    # Find actor
    vehicle = next((a for a in carla_world.get_actors() if a.id == vehicle_id), None)
    if vehicle is None:
        return None

    bp = carla_world.get_blueprint_library().find('sensor.camera.rgb')
    bp.set_attribute('image_size_x', str(width))
    bp.set_attribute('image_size_y', str(height))
    bp.set_attribute('fov', str(fov))

    cam_transform = carla.Transform(
        carla.Location(x=-5, z=3),
        carla.Rotation(pitch=-15)
    )

    cam = carla_world.spawn_actor(bp, cam_transform, attach_to=vehicle)
    vehicle_cameras[vehicle_id] = cam
    cam.listen(vehicle_callback_factory(vehicle_id))

    print(f"Camera attached to vehicle {vehicle_id}")
    return cam



def create_spectator_camera(width=800, height=600, fov=90):
    global spectator_camera, carla_world, latest_spectator_image

    # Destroy old spectator camera
    if spectator_camera is not None:
        spectator_camera.destroy()
        spectator_camera = None

    latest_spectator_image = None

    spectator = carla_world.get_spectator()

    # Fixed perpendicular BEV camera
    location = carla.Location(x=0, y=0, z=200)
    rotation = carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0)
    transform = carla.Transform(location, rotation)

    spectator.set_transform(transform)

    bp = carla_world.get_blueprint_library().find('sensor.camera.rgb')
    bp.set_attribute('image_size_x', str(width))
    bp.set_attribute('image_size_y', str(height))
    bp.set_attribute('fov', str(fov))

    spectator_camera = carla_world.spawn_actor(bp, transform, attach_to=spectator)
    spectator_camera.listen(spectator_callback)

    print("Spectator BEV camera created at z=200")
    return spectator_camera


# def create_spectator_camera(width=800, height=600, fov=90):
#     """Create a camera attached to the spectator as bird's-eye view"""
#     global camera_sensor, carla_world

#     if camera_sensor is not None:
#         camera_sensor.destroy()
#         camera_sensor = None

#     # Get spectator (free-flying camera)
#     spectator = carla_world.get_spectator()

#     # Get the map to determine center and appropriate altitude
#     carla_map = carla_world.get_map()

#     # Get map name to determine appropriate BEV settings
#     map_name = carla_map.name.split('/')[-1]

#     # Define BEV parameters based on map size
#     # These are approximate center coordinates and altitudes for common Carla maps
#     bev_config = {
#         'Town01': {'center': carla.Location(x=0, y=0, z=150), 'fov': 120},
#         'Town02': {'center': carla.Location(x=0, y=0, z=150), 'fov': 120},
#         'Town03': {'center': carla.Location(x=0, y=0, z=200), 'fov': 120},
#         'Town04': {'center': carla.Location(x=0, y=-0, z=200), 'fov': 120},
#         'Town05': {'center': carla.Location(x=0, y=0, z=200), 'fov': 120},
#         'Town06': {'center': carla.Location(x=0, y=0, z=150), 'fov': 120},
#         'Town07': {'center': carla.Location(x=0, y=0, z=150), 'fov': 120},
#         'Town10HD': {'center': carla.Location(x=50, y=0, z=200), 'fov': 120},
#     }

#     # Get config for current map or use default
#     config = bev_config.get(map_name, {'center': carla.Location(x=0, y=0, z=150), 'fov': 120})
#     bev_location = config['center']

#     # Create transform for BEV camera (looking straight down)
#     # Pitch -90 means looking straight down
#     bev_rotation = carla.Rotation(pitch=-180.0, yaw=00.0, roll=0.0)
#     bev_transform = carla.Transform(bev_location, bev_rotation)

#     # Position spectator at BEV location
#     spectator.set_transform(bev_transform)

#     # Get camera blueprint
#     blueprint_library = carla_world.get_blueprint_library()
#     camera_bp = blueprint_library.find('sensor.camera.rgb')
#     camera_bp.set_attribute('image_size_x', str(width))
#     camera_bp.set_attribute('image_size_y', str(height))
#     camera_bp.set_attribute('fov', str(fov))

#     # Spawn camera at spectator location
#     camera_sensor = carla_world.spawn_actor(camera_bp, bev_transform, attach_to=spectator)

#     # Register callback
#     camera_sensor.listen(image_callback)

#     print(f"BEV Camera created: {width}x{height}, FOV: {fov}")
#     print(f"Position: {bev_location.x:.1f}, {bev_location.y:.1f}, {bev_location.z:.1f}")
#     print(f"Rotation: pitch={bev_rotation.pitch}, yaw={bev_rotation.yaw}")
#     return camera_sensor


# def create_vehicle_camera(vehicle_id, width=800, height=600, fov=90):
#     """Create a camera attached to a specific vehicle"""
#     global camera_sensor, carla_world

#     if camera_sensor is not None:
#         camera_sensor.destroy()
#         camera_sensor = None

#     # Find vehicle by ID
#     vehicle = None
#     for actor in carla_world.get_actors():
#         if actor.id == vehicle_id and 'vehicle' in actor.type_id:
#             vehicle = actor
#             break

#     if vehicle is None:
#         return None

#     # Get camera blueprint
#     blueprint_library = carla_world.get_blueprint_library()
#     camera_bp = blueprint_library.find('sensor.camera.rgb')
#     camera_bp.set_attribute('image_size_x', str(width))
#     camera_bp.set_attribute('image_size_y', str(height))
#     camera_bp.set_attribute('fov', str(fov))

#     # Camera transform (slightly above and behind vehicle)
#     camera_transform = carla.Transform(
#         carla.Location(x=-5.0, z=3.0),
#         carla.Rotation(pitch=-15.0)
#     )

#     # Spawn camera attached to vehicle
#     camera_sensor = carla_world.spawn_actor(camera_bp, camera_transform, attach_to=vehicle)

#     # Register callback
#     camera_sensor.listen(image_callback)

#     print(f"Camera attached to vehicle {vehicle_id}")
#     return camera_sensor


def numpy_to_jpeg(image_array, quality=85):
    """Convert numpy array to JPEG bytes"""
    from PIL import Image

    img = Image.fromarray(image_array.astype('uint8'), 'RGB')
    output = io.BytesIO()
    img.save(output, format='JPEG', quality=quality)
    output.seek(0)
    return output


def generate_bev_map_image(scale=0.4, lane_step=2.0, margin=50.0):
    """Generate a BEV road map image similar to Carla no-rendering visualizer."""
    from PIL import Image, ImageDraw

    if carla_world is None:
        raise RuntimeError("Not connected to Carla")

    carla_map = carla_world.get_map()
    waypoints = carla_map.generate_waypoints(lane_step)
    if not waypoints:
        raise RuntimeError("No waypoints available to draw map")

    # Compute bounds
    max_x = max(w.transform.location.x for w in waypoints) + margin
    max_y = max(w.transform.location.y for w in waypoints) + margin
    min_x = min(w.transform.location.x for w in waypoints) - margin
    min_y = min(w.transform.location.y for w in waypoints) - margin

    width = max(max_x - min_x, max_y - min_y)
    pixels_per_meter = int(scale * 100)  # Rough scaling factor
    img_size = int(pixels_per_meter * width)
    if img_size <= 0:
        img_size = 1024

    def world_to_pixel(loc):
        x = pixels_per_meter * (loc.x - min_x)
        y = pixels_per_meter * (loc.y - min_y)
        return (int(x), int(img_size - y))  # flip Y for image coords

    # Create image
    img = Image.new('RGB', (img_size, img_size), (30, 34, 45))  # slate-like background
    draw = ImageDraw.Draw(img)

    # Draw roads as thick lines using lane edges
    for wp in waypoints:
        loc = wp.transform.location
        forward = wp.transform.get_forward_vector()
        right = carla.Vector3D(-forward.y, forward.x, 0)
        half_lane = 0.5 * wp.lane_width
        left_pt = carla.Location(
            loc.x - right.x * half_lane,
            loc.y - right.y * half_lane,
            loc.z
        )
        right_pt = carla.Location(
            loc.x + right.x * half_lane,
            loc.y + right.y * half_lane,
            loc.z
        )

        lpix = world_to_pixel(left_pt)
        rpix = world_to_pixel(right_pt)
        draw.line([lpix, rpix], fill=(70, 160, 255), width=max(1, int(4 * scale * 10)))

    return img


# API Routes

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'connected': carla_client is not None,
        'camera_active': camera_sensor is not None
    })


@app.route('/connect', methods=['POST'])
def connect():
    """Connect to Carla server"""
    data = request.json or {}
    host = data.get('host', 'localhost')
    port = data.get('port', 2000)

    success = connect_to_carla(host, port)

    if success:
        return jsonify({'success': True, 'message': 'Connected to Carla'})
    else:
        return jsonify({'success': False, 'error': 'Failed to connect'}), 500


@app.route('/capture', methods=['GET'])
def capture_spectator():
    if carla_world is None:
        return jsonify({'error': 'Not connected to Carla'}), 503

    width = int(request.args.get('width', 800))
    height = int(request.args.get('height', 600))
    fov = int(request.args.get('fov', 1200))
    quality = int(request.args.get('quality', 85))

    if spectator_camera is None:
        create_spectator_camera(width, height, fov)

    # Wait for an image
    timeout = 5.0
    start = time.time()
    while latest_spectator_image is None and (time.time() - start < timeout):
        time.sleep(0.05)

    if latest_spectator_image is None:
        return jsonify({'error': 'No image received from spectator camera'}), 500

    with image_lock:
        jpeg = numpy_to_jpeg(latest_spectator_image, quality)

    return send_file(jpeg, mimetype='image/jpeg')


@app.route('/map/bev', methods=['GET'])
def bev_map():
    """Return a top-down map image rendered from Carla waypoints."""
    try:
        scale = float(request.args.get('scale', 0.4))
        lane_step = float(request.args.get('lane_step', 2.0))
        img = generate_bev_map_image(scale=scale, lane_step=lane_step)
        output = io.BytesIO()
        img.save(output, format='PNG')
        output.seek(0)
        return send_file(output, mimetype='image/png')
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 503


@app.route('/capture/vehicle/<int:vehicle_id>', methods=['GET'])
def capture_vehicle(vehicle_id):
    if carla_world is None:
        return jsonify({'error': 'Not connected to Carla'}), 503

    width = int(request.args.get('width', 800))
    height = int(request.args.get('height', 600))
    fov = int(request.args.get('fov', 90))
    quality = int(request.args.get('quality', 85))

    cam = create_vehicle_camera(vehicle_id, width, height, fov)
    if cam is None:
        return jsonify({'error': f'Vehicle {vehicle_id} not found'}), 404

    # Reset current image
    latest_vehicle_images[vehicle_id] = None

    timeout = 5.0
    start = time.time()
    while latest_vehicle_images[vehicle_id] is None and (time.time() - start < timeout):
        time.sleep(0.05)

    if latest_vehicle_images[vehicle_id] is None:
        return jsonify({'error': 'No image received from vehicle camera'}), 500

    with image_lock:
        jpeg = numpy_to_jpeg(latest_vehicle_images[vehicle_id], quality)

    return send_file(jpeg, mimetype='image/jpeg')


@app.route('/vehicles', methods=['GET'])
def list_vehicles():
    """List all vehicles in the simulation"""
    if carla_world is None:
        return jsonify({'error': 'Not connected to Carla'}), 503

    vehicles = []
    for actor in carla_world.get_actors():
        if 'vehicle' in actor.type_id:
            location = actor.get_location()
            vehicles.append({
                'id': actor.id,
                'type': actor.type_id,
                'location': {
                    'x': location.x,
                    'y': location.y,
                    'z': location.z
                }
            })

    return jsonify({'vehicles': vehicles, 'count': len(vehicles)})


@app.route('/camera/spectator', methods=['POST'])
def set_spectator_camera():
    """Create/update spectator camera"""
    data = request.json or {}
    width = data.get('width', 800)
    height = data.get('height', 600)
    fov = data.get('fov', 90)

    try:
        create_spectator_camera(width, height, fov)
        return jsonify({'success': True, 'message': 'Spectator camera created'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# @app.route('/camera/destroy', methods=['POST'])
# def destroy_camera():
#     """Destroy current camera"""
#     global camera_sensor

#     if camera_sensor is not None:
#         camera_sensor.destroy()
#         camera_sensor = None
#         return jsonify({'success': True, 'message': 'Camera destroyed'})

#     return jsonify({'success': True, 'message': 'No camera to destroy'})


@app.route('/camera/destroy', methods=['POST'])
def destroy_all_cameras():
    global spectator_camera, vehicle_cameras

    if spectator_camera:
        spectator_camera.destroy()
        spectator_camera = None

    for cam in vehicle_cameras.values():
        try:
            cam.destroy()
        except:
            pass

    vehicle_cameras.clear()

    return jsonify({'success': True, 'message': 'All cameras destroyed'})


@app.route('/camera/destroy/<int:vehicle_id>', methods=['POST'])
def destroy_vehicle_camera(vehicle_id):
    if vehicle_id in vehicle_cameras:
        vehicle_cameras[vehicle_id].destroy()
        del vehicle_cameras[vehicle_id]
        return jsonify({'success': True, 'message': f'Camera for vehicle {vehicle_id} destroyed'})
    return jsonify({'success': False, 'error': 'Camera not found'}), 404



def generate_video_stream(mode='spectator', vehicle_id=None, width=800, height=600, fov=90, fps=10):
    """Generator for MJPEG video stream"""
    global spectator_camera, vehicle_cameras

    # Initialize camera based on mode
    if mode == 'spectator':
        create_spectator_camera(width, height, fov)
    elif mode == 'vehicle':
        cam = create_vehicle_camera(vehicle_id, width, height, fov)
        if cam is None:
            print(f"Vehicle {vehicle_id} not found, cannot stream.")
            return

    frame_delay = 1.0 / fps  # Target frame rate

    while True:
        try:
            start = time.time()
            timeout = 2.0
            frame = None

            # --- SPECTATOR CAMERA ---
            if mode == 'spectator':
                while latest_spectator_image is None and (time.time() - start < timeout):
                    time.sleep(0.02)

                if latest_spectator_image is not None:
                    with image_lock:
                        frame = latest_spectator_image.copy()

            # --- VEHICLE CAMERA ---
            elif mode == 'vehicle':
                while latest_vehicle_images.get(vehicle_id) is None and (time.time() - start < timeout):
                    time.sleep(0.02)

                if latest_vehicle_images.get(vehicle_id) is not None:
                    with image_lock:
                        frame = latest_vehicle_images[vehicle_id].copy()

            # No frame yet → skip iteration without crashing
            if frame is None:
                continue

            # Convert numpy → JPEG
            jpeg_data = numpy_to_jpeg(frame, quality=75)
            jpeg_bytes = jpeg_data.getvalue()

            # MJPEG multipart response
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" +
                jpeg_bytes +
                b"\r\n"
            )

            # Maintain fixed FPS
            elapsed = time.time() - start
            if elapsed < frame_delay:
                time.sleep(frame_delay - elapsed)

        except Exception as e:
            print(f"[STREAM ERROR] {e}")
            break





@app.route('/stream', methods=['GET'])
def stream_spectator():
    """MJPEG video stream from spectator camera"""
    if carla_world is None:
        return jsonify({'error': 'Not connected to Carla'}), 503

    # Get parameters
    width = int(request.args.get('width', 800))
    height = int(request.args.get('height', 600))
    fov = int(request.args.get('fov', 90))
    fps = int(request.args.get('fps', 10))

    return Response(
        generate_video_stream('spectator', None, width, height, fov, fps),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/stream/vehicle/<int:vehicle_id>', methods=['GET'])
def stream_vehicle(vehicle_id):
    """MJPEG video stream from vehicle camera"""
    if carla_world is None:
        return jsonify({'error': 'Not connected to Carla'}), 503

    # Get parameters
    width = int(request.args.get('width', 800))
    height = int(request.args.get('height', 600))
    fov = int(request.args.get('fov', 90))
    fps = int(request.args.get('fps', 10))

    return Response(
        generate_video_stream('vehicle', vehicle_id, width, height, fov, fps),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


def cleanup():
    """Cleanup resources"""
    global camera_sensor

    if camera_sensor is not None:
        try:
            camera_sensor.destroy()
        except:
            pass
        camera_sensor = None


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Carla Image Capture Service')
    parser.add_argument('--host', default='0.0.0.0', help='Flask server host')
    parser.add_argument('--port', type=int, default=5000, help='Flask server port')
    parser.add_argument('--carla-host', default='localhost', help='Carla server host')
    parser.add_argument('--carla-port', type=int, default=2000, help='Carla server port')
    parser.add_argument('--auto-connect', action='store_true', help='Auto-connect to Carla on startup')

    args = parser.parse_args()

    args.auto_connect = True

    # Auto-connect if requested with retry logic
    if args.auto_connect:
        max_retries = 10
        retry_delay = 5
        for attempt in range(max_retries):
            if connect_to_carla(args.carla_host, args.carla_port):
                break
            if attempt < max_retries - 1:
                print(f"Retrying connection in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
        else:
            print("Warning: Could not connect to Carla after multiple attempts. Service will start without connection.")

    try:
        print(f"Starting Image Capture Service on {args.host}:{args.port}")
        print(f"Carla server: {args.carla_host}:{args.carla_port}")
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        cleanup()
