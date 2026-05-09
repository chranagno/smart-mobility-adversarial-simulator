# Adversarial Attack Module

This module runs adversarial perception attacks while keeping the simulator
decoupled from model inference and gradient computation.

The simulator and `attack_client/` do not import PyTorch, OpenPCDet, YOLOP, or
checkpoints. They send sensor arrays to a separate FastAPI model server and get
back gradients, scalar losses, or inference outputs.

## Architecture

```text
CARLA / simulator process
  src/orchestrator.py
  attack_client/
    - NumPy attack update loops
    - HTTP calls through GradientClient

REST boundary

Model / gradient server
  model_server/
    - FastAPI endpoints
    - OpenPCDet / YOLOP loading
    - preprocessing, losses, backpropagation
```

## Implemented Attacks

| Attack | Runtime class | Reference |
| --- | --- | --- |
| LiDAR perturbation | `attack_client/attacks_3d.py::PointPerturbationAttack` | `ioulia_attacks/attack_custom.py` |
| LiDAR detachment | `attack_client/attacks_3d.py::PointDetachmentAttack` | `ioulia_attacks/remove_custom (1).py` |
| LiDAR attachment | `attack_client/attacks_3d.py::PointAttachmentAttack` | `ioulia_attacks/attach_custom.py` |
| Segmentation PGD | `attack_client/attacks_2d.py::PGDSegmentationAttack` | `ioulia_attacks/pgd_only_seg_losses.py` |
| Segmentation Square | `attack_client/attacks_2d.py::SquareAttack` | `ioulia_attacks/seg_square.py` |

3-D attacks currently use `second` successfully. `pointpillars` needs a
checkpoint/YAML with matching class heads. `yolop.pth` must be a real PyTorch
checkpoint before 2-D attacks can run.

## API Contract

`POST /api/v1/3d/gradients`

Client sends flattened point cloud data:

```json
{
  "model": "second",
  "points": [0.1, 1.2, -0.4, 0.8],
  "num_features": 4,
  "gt_boxes": [10.0, 0.0, 0.0, 4.0, 2.0, 1.5, 0.0],
  "num_box_features": 7
}
```

Server returns:

```json
{
  "model": "second",
  "gradients": [0.001, -0.002, 0.0, 0.0],
  "loss": 1.234,
  "latency_ms": 18.7
}
```

`POST /api/v1/2d/gradients` and `POST /api/v1/2d/query_loss` send RGB images in
`[0, 1]` plus optional CARLA segmentation masks:

- `da_mask`: drivable area, CARLA semantic tag `Roads == 7`
- `ll_mask`: lane lines, CARLA semantic tag `RoadLines == 6`
- `attack_mode`: `seg_drivable`, `seg_lane`, or `seg_both`

## Where Losses Run

Losses are computed only inside `model_server/model_registry.py`.

| Loss | Location |
| --- | --- |
| OpenPCDet detection proxy loss | `OpenPCDetModel._detection_loss` |
| YOLOP supervised segmentation loss | `YOLOPModel._seg_loss` |
| YOLOP query loss for Square Attack | `YOLOPModel.query_loss_2d` |

The attack client only applies attack updates: PGD projection, saliency-based
point removal, point attachment refinement, and square proposal search.

## Running The Model Server

The Docker image uses `djiajun1206/pcdet:pytorch1.6` as the base, installs
OpenPCDet under `/opt/openpcdet`, and mounts server code/checkpoints.

```bash
cd adversarial_attack_module/Docker
docker compose build model-server
docker compose up model-server
```

Check status:

```bash
curl -s http://localhost:8100/api/v1/models
```

Expected current status with the provided files:

- `second`: `active_family=openpcdet`, `is_stub=false`
- `pointpillars`: stub until checkpoint head and YAML class config match
- `centerpoint`: stub until checkpoint/config exist
- `yolop`: stub until `/checkpoints/yolop/yolop.pth` is a real checkpoint

## Scenario Use

Configure attacks in a scenario YAML:

```yaml
adversarial_attack:
  enabled: true
  server:
    url: "http://localhost:8100"
    timeout_s: 30
  attacks_3d:
    - name: perturbation_second
      enabled: true
      type: perturbation
      model: second
      epsilon: 0.10
      alpha: 0.01
      num_steps: 20
  sensor_targets:
    lidar_sensor_id: ego_lidar_top
    camera_sensor_id: ego_front_rgb
```

The simulator applies attacks only to the selected ego vehicle. Clean and
adversarial data are saved under the recorder output directory.

## Output Layout

Per-attack LiDAR outputs:

```text
adversarial/lidar/perturbation_second/000066_ego_lidar_top.pcd
adversarial/lidar/detachment_second/000066_ego_lidar_top.pcd
adversarial/lidar/attachment_second/000066_ego_lidar_top.pcd
```

Metadata:

```text
adversarial/metadata/000066_ego_lidar_top.json
adversarial/metadata/perturbation_second/000066_ego_lidar_top.json
```

Metadata records the frame, sensor id/type, clean and adversarial shapes,
attack config, loss trace, final loss, runtime, and GT boxes used. It is an
audit/debug file, not sensor data.

## Adding An Attack

1. Implement the attack loop in `attack_client/attacks_3d.py` or
   `attack_client/attacks_2d.py`.
2. Use `GradientClient` for model gradients, query losses, or inference.
3. Register the new `type` in `AttackOrchestrator.run_3d_attack()` or
   `run_2d_attack()`.
4. Add a YAML entry under `attacks_3d` or `attacks_2d`.

Keep model-specific logic out of `attack_client/`; it belongs in
`model_server/`.

## Adding A Model

1. Add a wrapper class in `model_server/model_registry.py` implementing the
   `BaseModel` methods needed by the attack.
2. Register the family in `MODEL_FAMILIES`.
3. Add an entry in `configs/models.yaml` with `family`, `config_path`,
   `checkpoint_path`, and `device`.
4. Mount or copy required configs/checkpoints into the Docker container.
5. Verify with:

```bash
curl -s http://localhost:8100/api/v1/models
```

## Decoupling Rule

`src/` and `attack_client/` may use NumPy, requests, and YAML. They must not
import Torch, OpenPCDet, YOLOP, checkpoints, voxel generators, or training
criteria. Those stay in `model_server/` and Docker.
