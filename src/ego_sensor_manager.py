# -*- coding: utf-8 -*-
"""
Ego vehicle sensor management and recording for co-simulation.

Follows the local perception and data-dump patterns:
  - Sensors are spawned via world.spawn_actor(bp, transform, attach_to=vehicle)
  - Callbacks store latest frame data
  - Recording saves images via cv2, lidar via open3d, metadata via YAML
  - V2X CAM messages saved as JSONL
"""

import base64
import json
import logging
import math
import os
import time
import weakref
from collections import deque
from datetime import datetime

import cv2
import numpy as np

try:
    import open3d as o3d
    HAS_OPEN3D = True
except ImportError:
    HAS_OPEN3D = False

import carla

try:
    from modules.scenario.yaml_utils import save_yaml
except ImportError:
    save_yaml = None

try:
    from modules.sensing.perception.sensor_transformation import get_bounding_box
except ImportError:
    get_bounding_box = None


def _rotation_matrix_from_rpy(roll_deg, pitch_deg, yaw_deg):
    """Return a 3x3 rotation matrix for roll/pitch/yaw in degrees."""
    roll = math.radians(float(roll_deg))
    pitch = math.radians(float(pitch_deg))
    yaw = math.radians(float(yaw_deg))

    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)

    rx = np.array([
        [1.0, 0.0, 0.0],
        [0.0, cr, -sr],
        [0.0, sr, cr],
    ])
    ry = np.array([
        [cp, 0.0, sp],
        [0.0, 1.0, 0.0],
        [-sp, 0.0, cp],
    ])
    rz = np.array([
        [cy, -sy, 0.0],
        [sy, cy, 0.0],
        [0.0, 0.0, 1.0],
    ])

    return rz @ ry @ rx


def _transform_matrix_from_config(transform_cfg):
    """Build a homogeneous 4x4 transform matrix from a sensor transform config."""
    x = float(transform_cfg.get('x', 0.0))
    y = float(transform_cfg.get('y', 0.0))
    z = float(transform_cfg.get('z', 0.0))
    roll = float(transform_cfg.get('roll', 0.0))
    pitch = float(transform_cfg.get('pitch', 0.0))
    yaw = float(transform_cfg.get('yaw', 0.0))

    matrix = np.eye(4, dtype=float)
    matrix[:3, :3] = _rotation_matrix_from_rpy(roll, pitch, yaw)
    matrix[:3, 3] = [x, y, z]
    return matrix


def _camera_intrinsics_from_attributes(attributes):
    """Compute pinhole intrinsics from CARLA camera blueprint attributes."""
    width = int(float(attributes.get('image_size_x', 800)))
    height = int(float(attributes.get('image_size_y', 600)))
    fov_deg = float(attributes.get('fov', 90.0))

    focal = width / (2.0 * math.tan(math.radians(fov_deg) / 2.0))
    cx = width / 2.0
    cy = height / 2.0

    return {
        'width': width,
        'height': height,
        'fov_deg': fov_deg,
        'fx': focal,
        'fy': focal,
        'cx': cx,
        'cy': cy,
        'K': [
            [focal, 0.0, cx],
            [0.0, focal, cy],
            [0.0, 0.0, 1.0],
        ],
    }


def _round_nested(value, digits=6):
    """Recursively round floats for compact, stable YAML output."""
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, np.floating):
        return round(float(value), digits)
    if isinstance(value, list):
        return [_round_nested(v, digits) for v in value]
    if isinstance(value, dict):
        return {k: _round_nested(v, digits) for k, v in value.items()}
    return value


def build_sensor_calibration(sensor_configs, base_frame='gt'):
    """Build per-sensor calibration metadata from recorder sensor configs."""
    calibration = {
        'base_frame': base_frame,
        'sensors': {}
    }

    for cfg in sensor_configs or []:
        sensor_id = cfg.get('id', 'unknown_sensor')
        sensor_type = cfg.get('type', 'unknown')
        transform_cfg = cfg.get('transform', {})
        attributes = cfg.get('attributes', {})
        matrix = _transform_matrix_from_config(transform_cfg)

        sensor_entry = {
            'type': sensor_type,
            'extrinsics': {
                'parent_frame': base_frame,
                'child_frame': sensor_id,
                'translation_m': {
                    'x': float(transform_cfg.get('x', 0.0)),
                    'y': float(transform_cfg.get('y', 0.0)),
                    'z': float(transform_cfg.get('z', 0.0)),
                },
                'rotation_deg': {
                    'roll': float(transform_cfg.get('roll', 0.0)),
                    'pitch': float(transform_cfg.get('pitch', 0.0)),
                    'yaw': float(transform_cfg.get('yaw', 0.0)),
                },
                'T_base_to_sensor': matrix.tolist(),
            },
        }

        if 'camera' in sensor_type:
            sensor_entry['intrinsics'] = _camera_intrinsics_from_attributes(attributes)

        calibration['sensors'][sensor_id] = sensor_entry

    return _round_nested(calibration)


def _top_semantic_tags(label_crop, limit=5):
    """Return the most common semantic tags in a crop for GT debugging."""
    if label_crop is None or label_crop.size == 0:
        return []
    values, counts = np.unique(label_crop, return_counts=True)
    order = np.argsort(counts)[::-1]
    top = []
    for idx in order[:limit]:
        top.append({
            'tag': int(values[idx]),
            'count': int(counts[idx]),
        })
    return top


def _carla_label_value(name):
    """Return a CARLA semantic label enum value when available."""
    try:
        return int(getattr(carla.CityObjectLabel, name))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# EgoSensorManager — attach / read / destroy CARLA sensors on the ego vehicle
# ---------------------------------------------------------------------------

class _SensorSlot:
    """Internal holder for one attached sensor and its latest data."""
    __slots__ = ('sensor_id', 'sensor_type', 'carla_sensor',
                 'image', 'semantic_labels', 'semantic_history',
                 'instance_ids', 'instance_tags', 'instance_history',
                 'pointcloud', 'semantic_lidar',
                 'frame', 'timestamp', '__weakref__')

    def __init__(self, sensor_id, sensor_type, carla_sensor):
        self.sensor_id = sensor_id
        self.sensor_type = sensor_type
        self.carla_sensor = carla_sensor
        self.image = None
        self.semantic_labels = None
        self.semantic_history = deque(maxlen=8)
        self.instance_ids = None
        self.instance_tags = None
        self.instance_history = deque(maxlen=8)
        self.pointcloud = None
        self.semantic_lidar = None
        self.frame = 0
        self.timestamp = None


class EgoSensorManager:
    """
    Manages CARLA sensor actors attached to the ego vehicle.

    Mirrors the local camera/lidar sensor approach:
    sensors are spawned as children of the vehicle actor and deliver
    data through ``listen()`` callbacks.
    """

    def __init__(self):
        self.ego_actor = None          # carla.Vehicle
        self.ego_sumo_id = None        # str  (SUMO id)
        self.ego_carla_id = None       # int  (CARLA id)
        self._slots = []               # list[_SensorSlot]

    # ------------------------------------------------------------------
    # Ego selection
    # ------------------------------------------------------------------
    def select_ego(self, scenario_manager, sumo_id=None):
        """
        Pick the ego vehicle from the co-simulation vehicle mapping.

        Parameters
        ----------
        scenario_manager : CoScenarioManager
            Must have ``sumo2carla_ids`` dict and ``world``.
        sumo_id : str or None
            If given, look up this specific SUMO vehicle.
            Otherwise pick the first entry in ``sumo2carla_ids``.

        Returns
        -------
        carla.Vehicle
        """
        mapping = scenario_manager.sumo2carla_ids
        if not mapping:
            raise RuntimeError('[EgoSensor] No SUMO vehicles spawned yet')

        if sumo_id and sumo_id in mapping:
            chosen_sumo_id = sumo_id
        elif sumo_id:
            logging.warning('[EgoSensor] sumo_id=%s not found, using first vehicle', sumo_id)
            chosen_sumo_id = next(iter(mapping))
        else:
            chosen_sumo_id = next(iter(mapping))

        carla_id = mapping[chosen_sumo_id]
        actor = scenario_manager.world.get_actor(carla_id)
        if actor is None:
            raise RuntimeError(f'[EgoSensor] CARLA actor {carla_id} not found')

        self.ego_actor = actor
        self.ego_sumo_id = chosen_sumo_id
        self.ego_carla_id = carla_id
        print(f'[EgoSensor] Selected ego vehicle: SUMO={chosen_sumo_id}, CARLA={carla_id}')
        return actor

    # ------------------------------------------------------------------
    # Sensor spawning
    # ------------------------------------------------------------------
    def spawn_sensors(self, sensor_configs, world, ego_actor=None):
        """
        Spawn sensors from the ``ego.sensors`` YAML list.

        Each entry must have at minimum:
            id, type, transform (x,y,z + optional roll,pitch,yaw), attributes

        Parameters
        ----------
        sensor_configs : list[dict]
            Sensor definitions from the scenario YAML.
        world : carla.World
        ego_actor : carla.Vehicle or None
            If None, uses ``self.ego_actor``.
        """
        if ego_actor is None:
            ego_actor = self.ego_actor
        if ego_actor is None:
            raise RuntimeError('[EgoSensor] No ego vehicle selected')

        bp_library = world.get_blueprint_library()

        for cfg in sensor_configs:
            sid = cfg['id']
            stype = cfg['type']

            bp = bp_library.find(stype)

            # Set blueprint attributes from config
            for attr_name, attr_val in cfg.get('attributes', {}).items():
                if bp.has_attribute(str(attr_name)):
                    bp.set_attribute(str(attr_name), str(attr_val))

            # Build transform
            t = cfg.get('transform', {})
            loc = carla.Location(
                x=float(t.get('x', 0)),
                y=float(t.get('y', 0)),
                z=float(t.get('z', 0))
            )
            rot = carla.Rotation(
                roll=float(t.get('roll', 0)),
                pitch=float(t.get('pitch', 0)),
                yaw=float(t.get('yaw', 0))
            )
            transform = carla.Transform(loc, rot)

            # Spawn attached to ego
            carla_sensor = world.spawn_actor(bp, transform, attach_to=ego_actor)

            slot = _SensorSlot(sid, stype, carla_sensor)
            weak_slot = weakref.ref(slot)

            if 'camera' in stype:
                carla_sensor.listen(
                    lambda event, ws=weak_slot: self._on_camera(ws, event))
            elif 'ray_cast_semantic' in stype:
                carla_sensor.listen(
                    lambda event, ws=weak_slot: self._on_semantic_lidar(ws, event))
            elif 'lidar' in stype:
                carla_sensor.listen(
                    lambda event, ws=weak_slot: self._on_lidar(ws, event))
            else:
                # Generic sensor — ignore data for now
                carla_sensor.listen(lambda _: None)

            self._slots.append(slot)
            print(f'[EgoSensor] Spawned sensor: {sid} ({stype})')

    # ------------------------------------------------------------------
    # Callbacks for camera and lidar sensor data.
    # ------------------------------------------------------------------
    @staticmethod
    def _on_camera(weak_slot, event):
        slot = weak_slot()
        if slot is None:
            return
        raw = np.frombuffer(event.raw_data, dtype=np.uint8)
        raw = raw.reshape((event.height, event.width, 4))
        if slot.sensor_type == 'sensor.camera.semantic_segmentation':
            # CARLA semantic camera stores the class tag in channel 2.
            slot.semantic_labels = np.copy(raw[:, :, 2])
            slot.semantic_history.append({
                'frame': int(event.frame),
                'labels': np.copy(slot.semantic_labels),
                'timestamp': float(event.timestamp),
            })
            event.convert(carla.ColorConverter.CityScapesPalette)
            raw = np.frombuffer(event.raw_data, dtype=np.uint8).reshape((event.height, event.width, 4))
        elif slot.sensor_type == 'sensor.camera.instance_segmentation':
            # CARLA raw image is BGRA:
            # red channel   -> semantic tag
            # green+blue    -> unique instance id
            instance_tags = raw[:, :, 2].astype(np.uint8)
            instance_ids = (
                (raw[:, :, 1].astype(np.uint32) << 8)
                + raw[:, :, 0].astype(np.uint32)
            )
            slot.instance_tags = instance_tags
            slot.instance_ids = instance_ids
            slot.instance_history.append({
                'frame': int(event.frame),
                'instance_tags': np.copy(instance_tags),
                'instance_ids': np.copy(instance_ids),
                'timestamp': float(event.timestamp),
            })
            event.convert(carla.ColorConverter.CityScapesPalette)
            raw = np.frombuffer(event.raw_data, dtype=np.uint8).reshape((event.height, event.width, 4))
        array = raw[:, :, :3]  # drop alpha
        slot.image = array
        slot.frame = event.frame
        slot.timestamp = event.timestamp

    @staticmethod
    def _on_lidar(weak_slot, event):
        slot = weak_slot()
        if slot is None:
            return
        data = np.copy(np.frombuffer(event.raw_data, dtype=np.float32))
        data = data.reshape((-1, 4))  # x, y, z, intensity
        slot.pointcloud = data
        slot.frame = event.frame
        slot.timestamp = event.timestamp

    @staticmethod
    def _on_semantic_lidar(weak_slot, event):
        slot = weak_slot()
        if slot is None:
            return
        data = np.frombuffer(event.raw_data, dtype=np.dtype([
            ('x', np.float32), ('y', np.float32), ('z', np.float32),
            ('cos_angle', np.float32), ('object_idx', np.uint32), ('object_tag', np.uint32)
        ]))
        slot.semantic_lidar = {
            'x': np.copy(data['x']),
            'y': np.copy(data['y']),
            'z': np.copy(data['z']),
            'cos_angle': np.copy(data['cos_angle']),
            'object_idx': np.copy(data['object_idx']),
            'object_tag': np.copy(data['object_tag']),
        }
        slot.frame = event.frame
        slot.timestamp = event.timestamp

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------
    def get_latest_data(self):
        """
        Return dict  sensor_id → {type, image/pointcloud, frame, timestamp}.
        Camera images are numpy BGR arrays.
        """
        result = {}
        for s in self._slots:
            entry = {
                'type': s.sensor_type,
                'frame': s.frame,
                'timestamp': s.timestamp,
            }
            if s.image is not None:
                entry['image'] = s.image
            if s.semantic_labels is not None:
                entry['semantic_labels'] = s.semantic_labels
            if s.instance_ids is not None:
                entry['instance_ids'] = s.instance_ids
            if s.instance_tags is not None:
                entry['instance_tags'] = s.instance_tags
            if s.pointcloud is not None:
                entry['pointcloud'] = s.pointcloud
            if s.semantic_lidar is not None:
                entry['semantic_lidar'] = s.semantic_lidar
            result[s.sensor_id] = entry
        return result

    def get_camera_jpeg_base64(self, quality=60):
        """
        Encode the first RGB camera image as base64 JPEG for WS streaming.
        Returns (sensor_id, b64_str) or (None, None).
        """
        for s in self._slots:
            if 'camera' in s.sensor_type and s.image is not None:
                _, buf = cv2.imencode('.jpg', s.image,
                                     [cv2.IMWRITE_JPEG_QUALITY, quality])
                return s.sensor_id, base64.b64encode(buf).decode('ascii')
        return None, None

    # ------------------------------------------------------------------
    # Telemetry helpers
    # ------------------------------------------------------------------
    def get_ego_telemetry(self):
        """
        Collect position, speed, acceleration from the ego CARLA actor.
        Returns dict or None.
        """
        v = self.ego_actor
        if v is None or not v.is_alive:
            return None

        transform = v.get_transform()
        velocity = v.get_velocity()
        accel = v.get_acceleration()
        speed_ms = (velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2) ** 0.5

        return {
            'carla_id': v.id,
            'sumo_id': self.ego_sumo_id,
            'x': float(transform.location.x),
            'y': float(transform.location.y),
            'z': float(transform.location.z),
            'roll': float(transform.rotation.roll),
            'pitch': float(transform.rotation.pitch),
            'yaw': float(transform.rotation.yaw),
            'speed_ms': float(speed_ms),
            'speed_kmh': float(speed_ms * 3.6),
            'velocity': {
                'x': float(velocity.x),
                'y': float(velocity.y),
                'z': float(velocity.z),
            },
            'acceleration': {
                'x': float(accel.x),
                'y': float(accel.y),
                'z': float(accel.z),
            },
        }

    def build_ground_truth(self, categories=None):
        """
        Build per-camera ground-truth detections in a COCO-like structure.
        """
        if get_bounding_box is None or self.ego_actor is None or not self.ego_actor.is_alive:
            return None

        categories = categories or ['vehicle']
        world = self.ego_actor.get_world()
        camera_slots = [s for s in self._slots if s.sensor_type == 'sensor.camera.rgb' and s.carla_sensor is not None]
        if not camera_slots:
            return None

        instance_by_camera = {}
        for slot in self._slots:
            if slot.sensor_type != 'sensor.camera.instance_segmentation':
                continue
            if slot.instance_ids is None:
                continue
            base_sensor_id = slot.sensor_id[:-8] if slot.sensor_id.endswith('_instseg') else slot.sensor_id
            instance_by_camera[base_sensor_id] = slot

        actor_filters = []
        if 'vehicle' in categories:
            actor_filters.append(('vehicle.*', 1, 'vehicle'))
        if 'walker' in categories:
            actor_filters.append(('walker.*', 2, 'pedestrian'))

        instance_tags_by_category = {
            'vehicle': {
                tag for tag in (
                    _carla_label_value('Car'),
                    _carla_label_value('Truck'),
                    _carla_label_value('Bus'),
                    _carla_label_value('Motorcycle'),
                    _carla_label_value('Bicycle'),
                    _carla_label_value('Train'),
                ) if tag is not None
            },
            'pedestrian': {
                tag for tag in (
                    _carla_label_value('Pedestrians'),
                    _carla_label_value('Rider'),
                ) if tag is not None
            },
        }

        actors = []
        for actor_filter, category_id, category_name in actor_filters:
            for actor in world.get_actors().filter(actor_filter):
                if actor.id == self.ego_actor.id:
                    continue
                actors.append((actor, category_id, category_name))

        sensors = {}
        summary = {
            'actors_total': len(actors),
            'annotations_total': 0,
            'per_sensor': {},
        }
        annotation_id = 1
        for slot in camera_slots:
            width = int(slot.carla_sensor.attributes.get('image_size_x', 800))
            height = int(slot.carla_sensor.attributes.get('image_size_y', 600))
            camera_annotations = []
            sensor_transform = slot.carla_sensor.get_transform()
            instance_slot = instance_by_camera.get(slot.sensor_id)
            instance_ids = None
            instance_tags = None
            instance_frame = None
            instance_frame_delta = None
            if instance_slot is not None:
                target_frame = int(slot.frame or 0)
                best_entry = None
                best_delta = None
                for entry in reversed(instance_slot.instance_history):
                    delta = abs(int(entry['frame']) - target_frame)
                    if best_delta is None or delta < best_delta:
                        best_entry = entry
                        best_delta = delta
                        if delta == 0:
                            break
                if best_entry is not None:
                    instance_tags = best_entry['instance_tags']
                    instance_ids = best_entry['instance_ids']
                    instance_frame = int(best_entry['frame'])
                    instance_frame_delta = int(best_delta)
            sensor_stats = {
                'actors_considered': 0,
                'projection_errors': 0,
                'empty_bbox': 0,
                'behind_camera': 0,
                'tiny_projected_bbox': 0,
                'camera_frame': int(slot.frame or 0),
                'instance_available': bool(instance_ids is not None and instance_tags is not None),
                'instance_frame': instance_frame,
                'instance_frame_delta': instance_frame_delta,
                'segmentation_invalid_crop': 0,
                'segmentation_no_matching_pixels': 0,
                'segmentation_no_components': 0,
                'segmentation_no_best_component': 0,
                'segmentation_empty_component': 0,
                'tiny_visible_bbox': 0,
                'annotations_kept': 0,
                'duplicate_instance_claimed': 0,
                'sample_filtered_actor_ids': [],
                'debug_boxes': [],
            }
            claimed_instance_ids = set()

            actor_candidates = []
            for actor, category_id, category_name in actors:
                try:
                    bbox_3d = get_bounding_box(actor, slot.carla_sensor, sensor_transform)
                except Exception:
                    bbox_3d = None
                actor_candidates.append((actor, category_id, category_name, bbox_3d))

            def candidate_depth(candidate):
                _, _, _, bbox_3d = candidate
                if bbox_3d is None or len(bbox_3d) == 0:
                    return float('inf')
                visible = bbox_3d[:, 2] > 0.1
                if not np.any(visible):
                    return float('inf')
                return float(np.min(bbox_3d[visible, 2]))

            for actor, category_id, category_name, bbox_3d in sorted(actor_candidates, key=candidate_depth):
                sensor_stats['actors_considered'] += 1
                if bbox_3d is None:
                    sensor_stats['projection_errors'] += 1
                    if len(sensor_stats['sample_filtered_actor_ids']) < 10:
                        sensor_stats['sample_filtered_actor_ids'].append({
                            'actor_id': actor.id,
                            'category': category_name,
                            'reason': 'projection_error',
                        })
                    continue

                if len(bbox_3d) == 0:
                    sensor_stats['empty_bbox'] += 1
                    if len(sensor_stats['sample_filtered_actor_ids']) < 10:
                        sensor_stats['sample_filtered_actor_ids'].append({
                            'actor_id': actor.id,
                            'category': category_name,
                            'reason': 'empty_bbox',
                        })
                    continue

                visible = bbox_3d[:, 2] > 0.1
                if not np.any(visible):
                    sensor_stats['behind_camera'] += 1
                    if len(sensor_stats['sample_filtered_actor_ids']) < 10:
                        sensor_stats['sample_filtered_actor_ids'].append({
                            'actor_id': actor.id,
                            'category': category_name,
                            'reason': 'behind_camera',
                        })
                    continue

                pts = bbox_3d[visible]
                min_x = max(0.0, float(np.min(pts[:, 0])))
                min_y = max(0.0, float(np.min(pts[:, 1])))
                max_x = min(float(width), float(np.max(pts[:, 0])))
                max_y = min(float(height), float(np.max(pts[:, 1])))
                projected_bbox = [min_x, min_y, max_x - min_x, max_y - min_y]
                debug_entry = {
                    'actor_id': actor.id,
                    'category': category_name,
                    'projected_bbox': projected_bbox,
                    'status': 'projected',
                }
                sensor_stats['debug_boxes'].append(debug_entry)
                if (max_x - min_x) <= 0.0 or (max_y - min_y) <= 0.0:
                    debug_entry['status'] = 'tiny_projected_bbox'
                    sensor_stats['tiny_projected_bbox'] += 1
                    if len(sensor_stats['sample_filtered_actor_ids']) < 10:
                        sensor_stats['sample_filtered_actor_ids'].append({
                            'actor_id': actor.id,
                            'category': category_name,
                            'reason': 'tiny_projected_bbox',
                            'projected_bbox': projected_bbox,
                        })
                    continue

                if instance_ids is not None and instance_tags is not None:
                    proj_x0 = max(0, int(math.floor(min_x)))
                    proj_y0 = max(0, int(math.floor(min_y)))
                    proj_x1 = min(width, int(math.ceil(max_x)))
                    proj_y1 = min(height, int(math.ceil(max_y)))
                    if proj_x1 <= proj_x0 or proj_y1 <= proj_y0:
                        debug_entry['status'] = 'segmentation_invalid_crop'
                        sensor_stats['segmentation_invalid_crop'] += 1
                        if len(sensor_stats['sample_filtered_actor_ids']) < 10:
                            sensor_stats['sample_filtered_actor_ids'].append({
                                'actor_id': actor.id,
                                'category': category_name,
                                'reason': 'segmentation_invalid_crop',
                            })
                        continue

                    bbox_w_px = proj_x1 - proj_x0
                    bbox_h_px = proj_y1 - proj_y0
                    pad_x = max(8, int(math.ceil(bbox_w_px * 0.15)))
                    pad_y = max(8, int(math.ceil(bbox_h_px * 0.15)))

                    crop_x0 = max(0, proj_x0 - pad_x)
                    crop_y0 = max(0, proj_y0 - pad_y)
                    crop_x1 = min(width, proj_x1 + pad_x)
                    crop_y1 = min(height, proj_y1 + pad_y)
                    if crop_x1 <= crop_x0 or crop_y1 <= crop_y0:
                        continue

                    id_crop = instance_ids[crop_y0:crop_y1, crop_x0:crop_x1]
                    tag_crop = instance_tags[crop_y0:crop_y1, crop_x0:crop_x1]
                    wanted_tags = instance_tags_by_category.get(category_name, set())
                    class_mask = np.isin(tag_crop, list(wanted_tags)) if wanted_tags else np.zeros_like(tag_crop, dtype=bool)
                    if not np.any(class_mask):
                        debug_entry['status'] = 'segmentation_no_matching_pixels'
                        debug_entry['search_crop'] = [crop_x0, crop_y0, crop_x1 - crop_x0, crop_y1 - crop_y0]
                        unique_ids, unique_counts = np.unique(id_crop, return_counts=True)
                        order = np.argsort(unique_counts)[::-1][:5]
                        debug_entry['top_instance_ids'] = [
                            {'instance_id': int(unique_ids[i]), 'count': int(unique_counts[i])}
                            for i in order
                        ]
                        debug_entry['top_semantic_tags'] = _top_semantic_tags(tag_crop)
                        sensor_stats['segmentation_no_matching_pixels'] += 1
                        if len(sensor_stats['sample_filtered_actor_ids']) < 10:
                            sensor_stats['sample_filtered_actor_ids'].append({
                                'actor_id': actor.id,
                                'category': category_name,
                                'reason': 'segmentation_no_matching_pixels',
                                'projected_bbox': projected_bbox,
                                'search_crop': [crop_x0, crop_y0, crop_x1 - crop_x0, crop_y1 - crop_y0],
                                'top_instance_ids': debug_entry['top_instance_ids'],
                                'top_semantic_tags': debug_entry['top_semantic_tags'],
                            })
                        continue

                    class_ids = id_crop[class_mask]
                    unique_ids, unique_counts = np.unique(class_ids, return_counts=True)
                    if len(unique_ids) == 0:
                        debug_entry['status'] = 'segmentation_no_components'
                        sensor_stats['segmentation_no_components'] += 1
                        continue

                    local_proj_x0 = proj_x0 - crop_x0
                    local_proj_y0 = proj_y0 - crop_y0
                    local_proj_x1 = proj_x1 - crop_x0
                    local_proj_y1 = proj_y1 - crop_y0
                    proj_center_x = (local_proj_x0 + local_proj_x1) / 2.0
                    proj_center_y = (local_proj_y0 + local_proj_y1) / 2.0

                    best_instance_id = None
                    best_score = None
                    for instance_id in unique_ids:
                        if int(instance_id) == 0:
                            continue
                        if int(instance_id) in claimed_instance_ids:
                            continue
                        instance_mask = (id_crop == instance_id) & class_mask
                        if not np.any(instance_mask):
                            continue
                        ys_i, xs_i = np.nonzero(instance_mask)
                        left = xs_i.min()
                        top = ys_i.min()
                        right = xs_i.max() + 1
                        bottom = ys_i.max() + 1
                        overlap_w = max(0, min(right, local_proj_x1) - max(left, local_proj_x0))
                        overlap_h = max(0, min(bottom, local_proj_y1) - max(top, local_proj_y0))
                        overlap_area = overlap_w * overlap_h
                        cx = float(xs_i.mean())
                        cy = float(ys_i.mean())
                        center_dist = math.hypot(cx - proj_center_x, cy - proj_center_y)
                        score = (overlap_area, -center_dist, int(instance_mask.sum()))
                        if best_score is None or score > best_score:
                            best_score = score
                            best_instance_id = instance_id

                    if best_instance_id is None:
                        if any(int(instance_id) in claimed_instance_ids for instance_id in unique_ids if int(instance_id) != 0):
                            debug_entry['status'] = 'duplicate_instance_claimed'
                            debug_entry['claimed_instance_ids'] = sorted(claimed_instance_ids)
                            sensor_stats['duplicate_instance_claimed'] += 1
                            if len(sensor_stats['sample_filtered_actor_ids']) < 10:
                                sensor_stats['sample_filtered_actor_ids'].append({
                                    'actor_id': actor.id,
                                    'category': category_name,
                                    'reason': 'duplicate_instance_claimed',
                                    'projected_bbox': projected_bbox,
                                    'search_crop': [crop_x0, crop_y0, crop_x1 - crop_x0, crop_y1 - crop_y0],
                                })
                        else:
                            debug_entry['status'] = 'segmentation_no_best_component'
                            sensor_stats['segmentation_no_best_component'] += 1
                        continue

                    visible_mask = ((id_crop == best_instance_id) & class_mask).astype(np.uint8)
                    ys, xs = np.nonzero(visible_mask)
                    debug_entry['matched_instance_id'] = int(best_instance_id)
                    min_x = float(crop_x0 + xs.min())
                    min_y = float(crop_y0 + ys.min())
                    max_x = float(crop_x0 + xs.max() + 1)
                    max_y = float(crop_y0 + ys.max() + 1)

                bbox_w = max_x - min_x
                bbox_h = max_y - min_y
                if bbox_w <= 1.0 or bbox_h <= 1.0:
                    debug_entry['status'] = 'tiny_visible_bbox'
                    debug_entry['visible_bbox'] = [min_x, min_y, bbox_w, bbox_h]
                    sensor_stats['tiny_visible_bbox'] += 1
                    if len(sensor_stats['sample_filtered_actor_ids']) < 10:
                        sensor_stats['sample_filtered_actor_ids'].append({
                            'actor_id': actor.id,
                            'category': category_name,
                            'reason': 'tiny_visible_bbox',
                            'projected_bbox': projected_bbox,
                            'visible_bbox': [min_x, min_y, bbox_w, bbox_h],
                        })
                    continue

                actor_transform = actor.get_transform()
                actor_bbox = actor.bounding_box
                location = actor_transform.location
                extent = actor_bbox.extent

                camera_annotations.append({
                    'id': annotation_id,
                    'actor_id': actor.id,
                    'category_id': category_id,
                    'category_name': category_name,
                    'bbox': [min_x, min_y, bbox_w, bbox_h],
                    'area': bbox_w * bbox_h,
                    'iscrowd': 0,
                    'score': 1.0,
                    'bbox_3d': {
                        'location': {
                            'x': float(location.x),
                            'y': float(location.y),
                            'z': float(location.z),
                        },
                        'rotation_deg': {
                            'roll': float(actor_transform.rotation.roll),
                            'pitch': float(actor_transform.rotation.pitch),
                            'yaw': float(actor_transform.rotation.yaw),
                        },
                        'extent': {
                            'x': float(extent.x),
                            'y': float(extent.y),
                            'z': float(extent.z),
                        },
                    },
                    'type_id': actor.type_id,
                })
                debug_entry['status'] = 'kept'
                debug_entry['visible_bbox'] = [min_x, min_y, bbox_w, bbox_h]
                if 'matched_instance_id' in debug_entry:
                    claimed_instance_ids.add(int(debug_entry['matched_instance_id']))
                annotation_id += 1
                sensor_stats['annotations_kept'] += 1

            sensors[slot.sensor_id] = {
                'width': width,
                'height': height,
                'annotations': camera_annotations,
            }
            summary['per_sensor'][slot.sensor_id] = sensor_stats
            summary['annotations_total'] += len(camera_annotations)

        return {
            'format': 'coco_like_per_frame',
            'vehicle': {
                'sumo_id': self.ego_sumo_id,
                'carla_id': self.ego_carla_id,
            },
            'categories': [
                {'id': 1, 'name': 'vehicle'},
                {'id': 2, 'name': 'pedestrian'},
            ],
            'sensors': sensors,
            'debug_stats': summary,
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def destroy(self):
        """Destroy all spawned sensor actors."""
        for s in self._slots:
            try:
                if s.carla_sensor is not None and s.carla_sensor.is_alive:
                    s.carla_sensor.stop()
                    s.carla_sensor.destroy()
            except Exception as e:
                logging.warning('[EgoSensor] Error destroying %s: %s', s.sensor_id, e)
        self._slots.clear()
        self.ego_actor = None
        print('[EgoSensor] All sensors destroyed')


# ---------------------------------------------------------------------------
# EgoRecorder — persist sensor data, telemetry, V2X messages to disk
# ---------------------------------------------------------------------------

class EgoRecorder:
    """
    Records ego vehicle data using the simulator data-dump conventions.

    Output structure::

        data_dumping/<save_time>/<vehicle_id>/
            images/               ← camera frames (PNG)
            lidar/                ← point cloud PCD files
            telemetry/            ← YAML per-frame metadata
            v2x_messages.json     ← appended CAM messages
    """

    def __init__(self):
        self.active = False
        self.base_dir = None
        self.images_dir = None
        self.lidar_dir = None
        self.telemetry_dir = None
        self.segmentation_dir = None
        self.semantic_lidar_dir = None
        self.ground_truth_dir = None
        self.ground_truth_debug_dir = None
        self.v2x_file = None
        self.calibration_file = None
        self._v2x_fh = None
        self._frame_count = 0
        self._image_count = 0
        self._lidar_count = 0
        self._v2x_count = 0
        self._start_time = None

    def start(self, vehicle_id, save_time=None, sensor_configs=None):
        """Create output directory tree and start recording."""
        if save_time is None:
            save_time = datetime.now().strftime('%Y_%m_%d_%H_%M_%S')

        # Follow DATA_DUMP_DIR env or the default local data_dumping path.
        current_path = os.path.dirname(os.path.realpath(__file__))
        root = os.getenv('DATA_DUMP_DIR') or \
            os.path.join(current_path, 'data_dumping')

        self.base_dir = os.path.join(root, save_time, str(vehicle_id))
        self.images_dir = os.path.join(self.base_dir, 'images')
        self.lidar_dir = os.path.join(self.base_dir, 'lidar')
        self.telemetry_dir = os.path.join(self.base_dir, 'telemetry')
        self.segmentation_dir = os.path.join(self.base_dir, 'segmentation')
        self.semantic_lidar_dir = os.path.join(self.base_dir, 'lidar_semantic')
        self.ground_truth_dir = os.path.join(self.base_dir, 'ground_truth')
        self.ground_truth_debug_dir = os.path.join(self.base_dir, 'ground_truth_debug')
        self.v2x_file = os.path.join(self.base_dir, 'v2x_messages.json')
        self.calibration_file = os.path.join(self.base_dir, 'calibration.yaml')

        for d in (self.images_dir, self.lidar_dir, self.telemetry_dir,
                  self.segmentation_dir, self.semantic_lidar_dir,
                  self.ground_truth_dir, self.ground_truth_debug_dir):
            os.makedirs(d, exist_ok=True)

        if sensor_configs:
            calibration = build_sensor_calibration(sensor_configs)
            self._write_yaml(calibration, self.calibration_file)

        self._v2x_fh = open(self.v2x_file, 'a')
        self._frame_count = 0
        self._image_count = 0
        self._lidar_count = 0
        self._v2x_count = 0
        self._start_time = time.time()
        self.active = True
        print(f'[EgoRecorder] Recording to: {self.base_dir}')
        if sensor_configs:
            print(f'[EgoRecorder] Calibration: {self.calibration_file}')

    def should_record(self, count):
        """
        Match simulator dump cadence: skip first 60 ticks, then every 2nd tick (10 Hz).
        """
        if count < 60:
            return False
        return count % 2 == 0

    def record_frame(self, sensor_id, sensor_type, data, count):
        """Save a single sensor frame to disk."""
        if not self.active:
            return

        if sensor_type in ('sensor.camera.semantic_segmentation', 'sensor.camera.instance_segmentation') and data.get('image') is not None:
            fname = f'{count:06d}_{sensor_id}.png'
            cv2.imwrite(os.path.join(self.segmentation_dir, fname), data['image'])
            self._image_count += 1

        elif 'camera' in sensor_type and data.get('image') is not None:
            fname = f'{count:06d}_{sensor_id}.png'
            cv2.imwrite(os.path.join(self.images_dir, fname), data['image'])
            self._image_count += 1

        elif sensor_type == 'sensor.lidar.ray_cast_semantic' and data.get('semantic_lidar') is not None:
            fname = f'{count:06d}_{sensor_id}.npz'
            np.savez_compressed(
                os.path.join(self.semantic_lidar_dir, fname),
                **data['semantic_lidar']
            )
            self._lidar_count += 1

        elif 'lidar' in sensor_type and data.get('pointcloud') is not None and HAS_OPEN3D:
            pc = data['pointcloud']
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(pc[:, :3])
            if pc.shape[1] >= 4:
                intensity = pc[:, 3:]
                colors = np.column_stack([intensity, np.zeros_like(intensity), np.zeros_like(intensity)])
                pcd.colors = o3d.utility.Vector3dVector(colors)
            fname = f'{count:06d}_{sensor_id}.pcd'
            o3d.io.write_point_cloud(
                os.path.join(self.lidar_dir, fname), pcd, write_ascii=True)
            self._lidar_count += 1

    def record_telemetry(self, telemetry_dict, count):
        """Save ego telemetry as YAML (following DataDumper convention)."""
        if not self.active or telemetry_dict is None:
            return
        fname = f'{count:06d}.yaml'
        path = os.path.join(self.telemetry_dir, fname)
        if not self._write_yaml(telemetry_dict, path):
            # Fallback: save as JSON
            fname = f'{count:06d}.json'
            with open(os.path.join(self.telemetry_dir, fname), 'w') as f:
                json.dump(telemetry_dict, f, indent=2)
        self._frame_count += 1

    @staticmethod
    def _write_yaml(data, path):
        """Write YAML with the local helper when available."""
        if save_yaml is None:
            return False
        save_yaml(data, path)
        return True

    def record_v2x(self, cam_messages, count):
        """Append V2X CAM messages for this frame as JSONL."""
        if not self.active or not cam_messages or self._v2x_fh is None:
            return
        for cam in cam_messages:
            record = {
                'frame': count,
                'timestamp': time.time(),
                **{k: v for k, v in cam.items()
                   if not isinstance(v, (np.ndarray, np.generic))}
            }
            # Convert any remaining numpy types
            self._v2x_fh.write(json.dumps(record, default=str) + '\n')
            self._v2x_count += 1
        self._v2x_fh.flush()

    def record_ground_truth(self, gt_payload, count):
        """Save per-frame ground-truth detections as JSON."""
        if not self.active or not gt_payload:
            return
        fname = f'{count:06d}_detections.json'
        with open(os.path.join(self.ground_truth_dir, fname), 'w') as f:
            json.dump(gt_payload, f, indent=2)

    def record_ground_truth_debug(self, gt_payload, sensor_data, count):
        """Draw GT 2D boxes on RGB images for debugging."""
        if not self.active or not gt_payload or not sensor_data:
            return

        sensors = gt_payload.get('sensors', {})
        per_sensor_stats = gt_payload.get('debug_stats', {}).get('per_sensor', {})
        for sensor_id, sensor_gt in sensors.items():
            sdata = sensor_data.get(sensor_id)
            if not sdata or sdata.get('image') is None:
                continue

            image = np.copy(sdata['image'])
            sensor_stats = per_sensor_stats.get(sensor_id, {})
            for dbg in sensor_stats.get('debug_boxes', []):
                bbox = dbg.get('projected_bbox')
                if not bbox or len(bbox) != 4:
                    continue
                x, y, w, h = bbox
                if w <= 0 or h <= 0:
                    continue
                pt1 = (int(round(x)), int(round(y)))
                pt2 = (int(round(x + w)), int(round(y + h)))
                actor_id = dbg.get('actor_id', '?')
                status = dbg.get('status', 'projected')
                cv2.rectangle(image, pt1, pt2, (0, 0, 255), 1)
                cv2.putText(
                    image,
                    f"{actor_id}:{status}",
                    (pt1[0], max(15, pt1[1] - 3)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    (0, 0, 255),
                    1,
                    cv2.LINE_AA,
                )

            annotations = sensor_gt.get('annotations', [])
            for ann in annotations:
                x, y, w, h = ann['bbox']
                pt1 = (int(round(x)), int(round(y)))
                pt2 = (int(round(x + w)), int(round(y + h)))
                category = ann.get('category_name', 'obj')
                actor_id = ann.get('actor_id', '?')
                color = (0, 255, 0) if category == 'vehicle' else (0, 165, 255)
                cv2.rectangle(image, pt1, pt2, color, 2)
                label = f"{category}:{actor_id}"
                cv2.putText(
                    image,
                    label,
                    (pt1[0], max(15, pt1[1] - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    1,
                    cv2.LINE_AA,
                )

            fname = f'{count:06d}_{sensor_id}_gt.png'
            cv2.imwrite(os.path.join(self.ground_truth_debug_dir, fname), image)

    def stop(self):
        """Flush files and print summary."""
        if self._v2x_fh and not self._v2x_fh.closed:
            self._v2x_fh.flush()
            self._v2x_fh.close()
            self._v2x_fh = None

        elapsed = time.time() - self._start_time if self._start_time else 0
        print(f'[EgoRecorder] Recording stopped after {elapsed:.1f}s')
        print(f'[EgoRecorder]   Images: {self._image_count}')
        print(f'[EgoRecorder]   LiDAR:  {self._lidar_count}')
        print(f'[EgoRecorder]   Telemetry frames: {self._frame_count}')
        print(f'[EgoRecorder]   V2X messages: {self._v2x_count}')
        if self.base_dir:
            print(f'[EgoRecorder]   Output: {self.base_dir}')

        self.active = False
        self._start_time = None
