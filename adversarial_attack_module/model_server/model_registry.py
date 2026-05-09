"""
Model registry for the adversarial attack model server.

Wraps OpenPCDet 3-D detectors (SECOND, PointPillars, CenterPoint) and YOLOP
for 2-D segmentation. Falls back to StubModel when dependencies are absent.

PyTorch is used exclusively here; the attack client never imports torch.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

import numpy as np
import yaml

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelConfig:
    """Configuration for a single model entry in models.yaml."""

    name: str
    family: str                          # "openpcdet" | "yolop" | "stub"
    device: str = "cuda:0"
    config_path: Optional[str] = None       # OpenPCDet model YAML
    checkpoint_path: Optional[str] = None
    extra: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base model
# ─────────────────────────────────────────────────────────────────────────────

class BaseModel(ABC):
    """Abstract wrapper around a detection or segmentation model."""

    def __init__(self, cfg: ModelConfig) -> None:
        self.cfg = cfg

    @abstractmethod
    def inference_3d(self, points: np.ndarray) -> Dict[str, Any]:
        """Run forward pass on a point cloud.

        Args:
            points: Float32 array of shape (N, 4) — x, y, z, intensity.

        Returns:
            Dict with keys ``boxes`` (M×7), ``scores`` (M,), ``labels`` (M,).
        """

    @abstractmethod
    def gradients_3d(
        self, points: np.ndarray, gt_boxes: np.ndarray
    ) -> Tuple[np.ndarray, float]:
        """Compute ∂L_det/∂P.

        Args:
            points: (N, 4) float32 point cloud.
            gt_boxes: (G, 7) float32 ground-truth boxes [x,y,z,dx,dy,dz,yaw].

        Returns:
            Tuple of (gradient array (N, 4), scalar loss).
        """

    @abstractmethod
    def inference_2d(self, image: np.ndarray) -> Dict[str, Any]:
        """Run forward pass on an RGB image.

        Args:
            image: (H, W, 3) uint8 or float32 image.

        Returns:
            Dict with keys ``drivable`` (H, W) and ``lanes`` (H, W).
        """

    @abstractmethod
    def gradients_2d(
        self,
        image: np.ndarray,
        da_mask: Optional[np.ndarray] = None,
        ll_mask: Optional[np.ndarray] = None,
        attack_mode: str = "seg_both",
    ) -> Tuple[np.ndarray, float]:
        """Compute ∂L_seg/∂x.

        Args:
            image: (H, W, 3) float32 image normalised to [0, 1].

        Returns:
            Tuple of (gradient array (H, W, 3), scalar loss).
        """

    @abstractmethod
    def query_loss_2d(
        self,
        image: np.ndarray,
        da_mask: Optional[np.ndarray] = None,
        ll_mask: Optional[np.ndarray] = None,
        attack_mode: str = "seg_both",
    ) -> float:
        """Return scalar segmentation loss without gradient (Square Attack).

        Args:
            image: (H, W, 3) float32 image.
        """

# ─────────────────────────────────────────────────────────────────────────────
# Stub model — synthetic responses for offline / CI use
# ─────────────────────────────────────────────────────────────────────────────

class StubModel(BaseModel):
    """Returns deterministic synthetic data. No GPU or model files required."""

    def inference_3d(self, points: np.ndarray) -> Dict[str, Any]:
        N = max(len(points), 1)
        return {
            "boxes": np.zeros((1, 7), dtype=np.float32),
            "scores": np.array([0.9], dtype=np.float32),
            "labels": np.array([0], dtype=np.int32),
        }

    def gradients_3d(
        self, points: np.ndarray, gt_boxes: np.ndarray
    ) -> Tuple[np.ndarray, float]:
        grad = np.random.randn(*points.shape).astype(np.float32) * 0.01
        return grad, 1.0

    def inference_2d(self, image: np.ndarray) -> Dict[str, Any]:
        H, W = image.shape[:2]
        return {
            "drivable": np.zeros((H, W), dtype=np.uint8),
            "lanes": np.zeros((H, W), dtype=np.uint8),
        }

    def gradients_2d(
        self,
        image: np.ndarray,
        da_mask: Optional[np.ndarray] = None,
        ll_mask: Optional[np.ndarray] = None,
        attack_mode: str = "seg_both",
    ) -> Tuple[np.ndarray, float]:
        grad = np.random.randn(*image.shape).astype(np.float32) * 0.01
        return grad, 1.0

    def query_loss_2d(
        self,
        image: np.ndarray,
        da_mask: Optional[np.ndarray] = None,
        ll_mask: Optional[np.ndarray] = None,
        attack_mode: str = "seg_both",
    ) -> float:
        return float(np.random.rand())


# ─────────────────────────────────────────────────────────────────────────────
# OpenPCDet model (SECOND / PointPillars / CenterPoint)
# ─────────────────────────────────────────────────────────────────────────────

try:
    import torch
    import torch.nn as nn
    HAS_TORCH = True
    TORCH_IMPORT_ERROR = None
except ImportError as exc:
    HAS_TORCH = False
    TORCH_IMPORT_ERROR = exc

try:
    from easydict import EasyDict
    from pcdet.config import cfg as pcdet_global_cfg, cfg_from_yaml_file
    from pcdet.datasets.processor.data_processor import DataProcessor
    from pcdet.datasets.processor.point_feature_encoder import PointFeatureEncoder
    from pcdet.models import build_network, load_data_to_gpu
    HAS_OPENPCDET = True
    OPENPCDET_IMPORT_ERROR = None
except ImportError as exc:
    HAS_OPENPCDET = False
    OPENPCDET_IMPORT_ERROR = exc


def _cfg_get(obj: Any, key: str, default: Any = None) -> Any:
    """Read from EasyDict/dict configs without depending on either type."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class _OpenPCDetDatasetAdapter:
    """Minimal dataset object required by OpenPCDet model construction.

    The model server receives already-captured simulator point clouds over REST,
    so it does not need a real OpenPCDet dataset on disk.  OpenPCDet still uses
    dataset metadata while constructing detector modules.
    """

    def __init__(self, data_cfg: Any, class_names: List[str]) -> None:
        self.class_names = class_names
        self.training = False
        self.point_cloud_range = np.asarray(
            _cfg_get(data_cfg, "POINT_CLOUD_RANGE", [-75.2, -75.2, -4.0, 75.2, 75.2, 4.0]),
            dtype=np.float32,
        )
        self.point_feature_encoder = PointFeatureEncoder(
            _cfg_get(data_cfg, "POINT_FEATURE_ENCODING"),
            point_cloud_range=self.point_cloud_range,
        )
        self.voxel_size = self._find_voxel_size(data_cfg)
        self.grid_size = np.round(
            (self.point_cloud_range[3:6] - self.point_cloud_range[0:3]) / self.voxel_size
        ).astype(np.int64)
        self.depth_downsample_factor = None
        self.data_processor = DataProcessor(
            _cfg_get(data_cfg, "DATA_PROCESSOR", []),
            point_cloud_range=self.point_cloud_range,
            training=False,
            num_point_features=self.point_feature_encoder.num_point_features,
        )

    @staticmethod
    def _find_voxel_size(data_cfg: Any) -> np.ndarray:
        processors = _cfg_get(data_cfg, "DATA_PROCESSOR", []) or []
        for processor in processors:
            if _cfg_get(processor, "NAME") == "transform_points_to_voxels":
                return np.asarray(_cfg_get(processor, "VOXEL_SIZE"), dtype=np.float32)
        return np.asarray([0.1, 0.1, 0.2], dtype=np.float32)


class OpenPCDetModel(BaseModel):
    """Wraps an OpenPCDet model for inference and gradient computation.

    Supports SECOND, PointPillars, and CenterPoint via a shared interface.
    Requires OpenPCDet and PyTorch with CUDA.
    """

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__(cfg)
        missing = []
        if not HAS_TORCH:
            missing.append(f"PyTorch ({TORCH_IMPORT_ERROR})")
        if not HAS_OPENPCDET:
            missing.append(f"OpenPCDet ({OPENPCDET_IMPORT_ERROR})")
        if missing:
            raise RuntimeError(
                f"{', '.join(missing)} required for OpenPCDetModel is not importable. "
                "Run the model server in the GPU Docker image or install server dependencies."
            )
        self._device = torch.device(cfg.device)
        self._model = self._load(cfg)
        logger.info("[ModelRegistry] Loaded OpenPCDet model: %s on %s", cfg.name, cfg.device)

    @staticmethod
    def _extract_state_dict(checkpoint: Any) -> dict:
        if not isinstance(checkpoint, dict):
            return checkpoint
        for key in ("model_state", "state_dict", "model", "model_state_dict"):
            value = checkpoint.get(key)
            if isinstance(value, dict):
                return value
        return checkpoint

    @staticmethod
    def _adapt_state_dict_for_model(model: "nn.Module", state_dict: dict) -> dict:
        target_state = model.state_dict()
        adapted = {}
        converted_sparse_weights = 0

        for key, value in state_dict.items():
            clean_key = key[7:] if key.startswith("module.") else key
            target = target_state.get(clean_key)

            if (
                target is not None and
                hasattr(value, "shape") and
                tuple(value.shape) != tuple(target.shape) and
                len(value.shape) == 5
            ):
                # Some old spconv checkpoints store sparse conv weights as
                # [out, kx, ky, kz, in], while the installed op expects
                # [kx, ky, kz, in, out].
                transposed = value.permute(1, 2, 3, 4, 0).contiguous()
                if tuple(transposed.shape) == tuple(target.shape):
                    value = transposed
                    converted_sparse_weights += 1

            adapted[clean_key] = value

        if converted_sparse_weights:
            logger.info(
                "[ModelRegistry] Converted %d sparse conv checkpoint tensors",
                converted_sparse_weights,
            )
        return adapted

    def _load(self, cfg: ModelConfig) -> "nn.Module":
        model_cfg = EasyDict()
        cfg_from_yaml_file(cfg.config_path, model_cfg)
        model_cfg.TAG = cfg.name
        dataset = _OpenPCDetDatasetAdapter(
            model_cfg.DATA_CONFIG,
            list(model_cfg.CLASS_NAMES),
        )
        self._dataset = dataset
        model = build_network(
            model_cfg=model_cfg.MODEL,
            num_class=len(model_cfg.CLASS_NAMES),
            dataset=dataset,
        )
        checkpoint = torch.load(cfg.checkpoint_path, map_location=self._device)
        state_dict = self._adapt_state_dict_for_model(
            model,
            self._extract_state_dict(checkpoint),
        )
        model.load_state_dict(state_dict, strict=True)
        model.to(self._device)
        model.eval()
        return model

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _preprocess_points(self, points: np.ndarray) -> Tuple[dict, dict]:
        data_dict = {
            "points": points.astype(np.float32, copy=False),
            "frame_id": "0",
        }
        data_dict = self._dataset.point_feature_encoder.forward(data_dict)
        data_dict = self._dataset.data_processor.forward(data_dict=data_dict)

        batch = {}
        if "voxels" in data_dict:
            batch["voxels"] = data_dict["voxels"]
        if "voxel_num_points" in data_dict:
            batch["voxel_num_points"] = data_dict["voxel_num_points"]
        if "voxel_coords" in data_dict:
            batch["voxel_coords"] = np.pad(
                data_dict["voxel_coords"],
                ((0, 0), (1, 0)),
                mode="constant",
                constant_values=0,
            )
        if "points" in data_dict:
            batch["points"] = np.pad(
                data_dict["points"],
                ((0, 0), (1, 0)),
                mode="constant",
                constant_values=0,
            )
        batch["batch_size"] = 1
        load_data_to_gpu(batch)
        return batch, data_dict

    def _points_to_batch(self, points: np.ndarray) -> dict:
        """Package a (N,4) point cloud into an OpenPCDet batch dict."""
        batch, _ = self._preprocess_points(points)
        return batch

    def _raw_points_to_batch(self, points: np.ndarray) -> dict:
        pts = torch.tensor(points, dtype=torch.float32, device=self._device)
        batch = {
            "points": torch.cat(
                [torch.zeros((len(pts), 1), device=self._device), pts], dim=1
            ),
            "batch_size": 1,
            "frame_id": torch.tensor([0]),
        }
        return batch

    @staticmethod
    def _first_prediction(preds: Any) -> dict:
        if isinstance(preds, tuple):
            preds = preds[0]
        if isinstance(preds, list):
            return preds[0] if preds else {}
        return preds

    @staticmethod
    def _voxel_grad_to_points(
        original_points: np.ndarray,
        data_dict: dict,
        voxel_grad: np.ndarray,
    ) -> np.ndarray:
        grad_full = np.zeros_like(original_points, dtype=np.float32)
        processed_points = data_dict.get("points")
        voxels = data_dict.get("voxels")
        voxel_num_points = data_dict.get("voxel_num_points")
        if processed_points is None or voxels is None or voxel_num_points is None:
            return grad_full

        lookup = defaultdict(deque)
        for idx, point in enumerate(original_points.astype(np.float32, copy=False)):
            lookup[tuple(np.round(point[:4], 5))].append(idx)

        for voxel_idx, num_points in enumerate(voxel_num_points.astype(np.int64)):
            for point_idx in range(int(num_points)):
                point = voxels[voxel_idx, point_idx]
                key = tuple(np.round(point[:4], 5))
                if not lookup[key]:
                    continue
                original_idx = lookup[key].popleft()
                cols = min(grad_full.shape[1], voxel_grad.shape[2])
                grad_full[original_idx, :cols] = voxel_grad[voxel_idx, point_idx, :cols]
        return grad_full

    def _detection_loss(
        self, preds: dict, gt_boxes: np.ndarray
    ) -> "torch.Tensor":
        """Compute a proxy detection loss for adversarial gradient generation.

        When ground-truth boxes are provided, focus the objective on
        predictions closest to those boxes.  Otherwise fall back to
        −max(pred_scores) so the gradient still suppresses confident
        detections.
        """
        import torch
        if "loss" in preds:
            return preds["loss"]

        scores = preds.get("pred_scores", None)
        boxes = preds.get("pred_boxes", None)
        if (
            gt_boxes is not None and len(gt_boxes) > 0 and
            scores is not None and boxes is not None and
            torch.is_tensor(scores) and torch.is_tensor(boxes) and
            len(scores) > 0 and len(boxes) > 0
        ):
            gt = torch.as_tensor(gt_boxes[:, :7], dtype=boxes.dtype, device=boxes.device)
            pred_centers = boxes[:, :3]
            gt_centers = gt[:, :3]
            distances = torch.cdist(pred_centers, gt_centers)
            nearest_dist, nearest_idx = distances.min(dim=0)
            matched_scores = scores[nearest_idx]

            # Gradient ascent on this loss reduces confidence near GT objects
            # and pushes matched predictions away from their GT centers.
            return -matched_scores.mean() + 0.05 * nearest_dist.mean()

        if scores is not None and len(scores) > 0:
            return -scores.max()

        # Absolute last resort
        return torch.tensor(1.0, requires_grad=True, device=self._device)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def inference_3d(self, points: np.ndarray) -> Dict[str, Any]:
        import torch
        with torch.no_grad():
            batch = self._points_to_batch(points)
            preds = self._model(batch)
        out = self._first_prediction(preds)
        return {
            "boxes": _to_numpy(out.get("pred_boxes", np.zeros((0, 7)))),
            "scores": _to_numpy(out.get("pred_scores", np.zeros(0))),
            "labels": _to_numpy(out.get("pred_labels", np.zeros(0, dtype=np.int32))),
        }

    def gradients_3d(
        self, points: np.ndarray, gt_boxes: np.ndarray
    ) -> Tuple[np.ndarray, float]:
        import torch
        batch, data_dict = self._preprocess_points(points)
        if "voxels" not in batch:
            return np.zeros_like(points, dtype=np.float32), 1.0
        batch["voxels"].requires_grad_(True)

        preds = self._model(batch)
        out = self._first_prediction(preds)
        loss = self._detection_loss(out, gt_boxes)
        loss.backward()

        voxel_grad = batch["voxels"].grad
        if voxel_grad is None:
            grad_full = np.zeros_like(points, dtype=np.float32)
        else:
            grad_full = self._voxel_grad_to_points(
                points,
                data_dict,
                voxel_grad.detach().cpu().numpy(),
            )
        return grad_full, float(loss.item())

    # ------------------------------------------------------------------
    # 2-D stubs (OpenPCDet models do not process images)
    # ------------------------------------------------------------------
    def inference_2d(self, image: np.ndarray) -> Dict[str, Any]:
        raise NotImplementedError("OpenPCDet models handle point clouds, not images.")

    def gradients_2d(
        self,
        image: np.ndarray,
        da_mask: Optional[np.ndarray] = None,
        ll_mask: Optional[np.ndarray] = None,
        attack_mode: str = "seg_both",
    ) -> Tuple[np.ndarray, float]:
        raise NotImplementedError("OpenPCDet models handle point clouds, not images.")

    def query_loss_2d(
        self,
        image: np.ndarray,
        da_mask: Optional[np.ndarray] = None,
        ll_mask: Optional[np.ndarray] = None,
        attack_mode: str = "seg_both",
    ) -> float:
        raise NotImplementedError("OpenPCDet models handle point clouds, not images.")


# ─────────────────────────────────────────────────────────────────────────────
# YOLOP model (drivable-area + lane-line segmentation)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from lib.models import get_net as yolop_get_net
    HAS_YOLOP = True
    YOLOP_IMPORT_ERROR = None
except ImportError as exc:
    HAS_YOLOP = False
    YOLOP_IMPORT_ERROR = exc


class YOLOPModel(BaseModel):
    """Wraps YOLOP for differentiable semantic segmentation attacks.

    Computes ∂L_seg/∂x where L_seg is the cross-entropy loss summed over
    the drivable-area and lane-line segmentation heads.
    """

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__(cfg)
        missing = []
        if not HAS_TORCH:
            missing.append(f"PyTorch ({TORCH_IMPORT_ERROR})")
        if not HAS_YOLOP:
            missing.append(f"YOLOP ({YOLOP_IMPORT_ERROR})")
        if missing:
            raise RuntimeError(
                f"{', '.join(missing)} required for YOLOPModel is not importable. "
                "Run the model server in the GPU Docker image or install server dependencies."
            )
        self._device = torch.device(cfg.device)
        input_size = cfg.extra.get("input_size", [640, 640])
        self._input_h, self._input_w = int(input_size[0]), int(input_size[1])
        self._mean = tuple(cfg.extra.get("mean", [0.485, 0.456, 0.406]))
        self._std = tuple(cfg.extra.get("std", [0.229, 0.224, 0.225]))
        self._model = self._load(cfg)
        logger.info("[ModelRegistry] Loaded YOLOP model on %s", cfg.device)

    def _load(self, cfg: ModelConfig) -> "nn.Module":
        import torch
        model = yolop_get_net(cfg=None)
        try:
            checkpoint = torch.load(cfg.checkpoint_path, map_location=self._device)
        except Exception as exc:
            raise RuntimeError(
                f"failed to load YOLOP checkpoint '{cfg.checkpoint_path}'. "
                "Expected a real PyTorch checkpoint; check for an HTML download, "
                f"Git-LFS pointer, or empty file. Original error: {exc}"
            ) from exc

        if isinstance(checkpoint, dict):
            state_dict = (
                checkpoint.get("state_dict")
                or checkpoint.get("model")
                or checkpoint.get("model_state_dict")
                or checkpoint
            )
        else:
            state_dict = checkpoint

        if isinstance(state_dict, dict):
            state_dict = {
                key[7:] if key.startswith("module.") else key: value
                for key, value in state_dict.items()
            }
        model.load_state_dict(state_dict, strict=False)
        model.to(self._device)
        model.eval()
        return model

    @staticmethod
    def _as_unit_rgb(image: np.ndarray) -> np.ndarray:
        img = image.astype(np.float32)
        if img.size and float(np.nanmax(img)) > 1.5:
            img = img / 255.0
        return np.clip(img, 0.0, 1.0)

    def _normalise_tensor(self, img_t: "torch.Tensor") -> "torch.Tensor":
        import torch
        mean = torch.as_tensor(self._mean, dtype=img_t.dtype, device=img_t.device).view(1, 3, 1, 1)
        std = torch.as_tensor(self._std, dtype=img_t.dtype, device=img_t.device).view(1, 3, 1, 1)
        return (img_t - mean) / std

    def _preprocess(self, image: np.ndarray) -> "torch.Tensor":
        """Resize + normalise image to YOLOP input format (1, 3, H, W)."""
        import cv2
        import torch
        img = cv2.resize(self._as_unit_rgb(image), (self._input_w, self._input_h))
        img = torch.tensor(img, dtype=torch.float32, device=self._device)
        img = img.permute(2, 0, 1).unsqueeze(0)   # (1, 3, H, W)
        return self._normalise_tensor(img)

    def _entropy_loss(self, da_pred: "torch.Tensor", ll_pred: "torch.Tensor") -> "torch.Tensor":
        """Fallback loss used only when CARLA masks are not supplied.

        We want the model to produce high-entropy (confused) predictions, so
        we maximise the mean softmax entropy across both heads.
        """
        import torch.nn.functional as F
        loss = torch.tensor(0.0, device=self._device)
        for pred in (da_pred, ll_pred):
            if pred is None:
                continue
            probs = F.softmax(pred, dim=1)
            log_probs = F.log_softmax(pred, dim=1)
            # Negative entropy (we negate later in the attack ascent step)
            loss = loss - (probs * log_probs).sum(dim=1).mean()
        return loss

    def _bce_or_ce_seg_loss(
        self,
        pred: "Optional[torch.Tensor]",
        target: Optional[np.ndarray],
    ) -> "torch.Tensor":
        """YOLOP segmentation loss for one head.

        The original scripts use YOLOP's BCE segmentation criterion.  Some
        checkpoints expose a single-channel head, others expose two logits.
        This supports both while keeping the loss on the gradient server.
        """
        import cv2
        import torch
        import torch.nn.functional as F

        if pred is None or target is None:
            return torch.tensor(0.0, dtype=torch.float32, device=self._device)

        if pred.ndim == 3:
            pred = pred.unsqueeze(0)
        if pred.ndim != 4:
            raise ValueError(f"Expected YOLOP segmentation logits as BCHW, got {tuple(pred.shape)}")

        _, channels, out_h, out_w = pred.shape
        tgt = cv2.resize(
            target.astype(np.float32), (out_w, out_h),
            interpolation=cv2.INTER_NEAREST,
        )
        tgt = (tgt > 0.5).astype(np.float32)

        if channels == 1:
            tgt_t = torch.as_tensor(tgt, dtype=pred.dtype, device=pred.device).view(1, 1, out_h, out_w)
            return F.binary_cross_entropy_with_logits(pred, tgt_t)

        if channels == 2:
            tgt_t = torch.as_tensor(tgt, dtype=torch.long, device=pred.device).view(1, out_h, out_w)
            return F.cross_entropy(pred, tgt_t)

        tgt_t = torch.as_tensor(tgt, dtype=pred.dtype, device=pred.device).view(1, 1, out_h, out_w)
        tgt_t = tgt_t.expand(1, channels, out_h, out_w)
        return F.binary_cross_entropy_with_logits(pred, tgt_t)

    def _seg_loss(
        self,
        da_pred: "torch.Tensor",
        ll_pred: "torch.Tensor",
        da_mask: Optional[np.ndarray] = None,
        ll_mask: Optional[np.ndarray] = None,
        attack_mode: str = "seg_both",
    ) -> "torch.Tensor":
        import torch

        attack_mode = (attack_mode or "seg_both").lower()
        has_targets = da_mask is not None or ll_mask is not None
        if not has_targets:
            return self._entropy_loss(da_pred, ll_pred)

        loss = torch.tensor(0.0, dtype=torch.float32, device=self._device)
        if attack_mode in ("seg_drivable", "seg_both"):
            loss = loss + self._bce_or_ce_seg_loss(da_pred, da_mask)
        if attack_mode in ("seg_lane", "seg_both"):
            loss = loss + self._bce_or_ce_seg_loss(ll_pred, ll_mask)
        if attack_mode not in ("seg_drivable", "seg_lane", "seg_both"):
            raise ValueError(
                "attack_mode must be 'seg_drivable', 'seg_lane', or 'seg_both'"
            )
        return loss

    def inference_2d(self, image: np.ndarray) -> Dict[str, Any]:
        import torch
        with torch.no_grad():
            img_t = self._preprocess(image)
            outputs = self._model(img_t)
        da_pred = outputs[1] if len(outputs) > 1 else None
        ll_pred = outputs[2] if len(outputs) > 2 else None
        da_mask = _seg_argmax(da_pred, (image.shape[0], image.shape[1]))
        ll_mask = _seg_argmax(ll_pred, (image.shape[0], image.shape[1]))
        return {"drivable": da_mask, "lanes": ll_mask}

    def gradients_2d(
        self,
        image: np.ndarray,
        da_mask: Optional[np.ndarray] = None,
        ll_mask: Optional[np.ndarray] = None,
        attack_mode: str = "seg_both",
    ) -> Tuple[np.ndarray, float]:
        import torch
        import cv2
        resized = cv2.resize(self._as_unit_rgb(image), (self._input_w, self._input_h))
        img_f = resized.astype(np.float32)
        img_t = torch.tensor(img_f, dtype=torch.float32, device=self._device)
        img_t = img_t.permute(2, 0, 1).unsqueeze(0).requires_grad_(True)

        outputs = self._model(self._normalise_tensor(img_t))
        da_pred = outputs[1] if len(outputs) > 1 else None
        ll_pred = outputs[2] if len(outputs) > 2 else None
        loss = self._seg_loss(da_pred, ll_pred, da_mask, ll_mask, attack_mode)
        loss.backward()

        grad = img_t.grad.squeeze(0).permute(1, 2, 0).detach().cpu().numpy()  # (H,W,3)
        # Resize gradient back to original image dimensions
        grad = cv2.resize(grad, (image.shape[1], image.shape[0]))
        return grad.astype(np.float32), float(loss.item())

    def query_loss_2d(
        self,
        image: np.ndarray,
        da_mask: Optional[np.ndarray] = None,
        ll_mask: Optional[np.ndarray] = None,
        attack_mode: str = "seg_both",
    ) -> float:
        import torch
        with torch.no_grad():
            img_t = self._preprocess(image)
            outputs = self._model(img_t)
        da_pred = outputs[1] if len(outputs) > 1 else None
        ll_pred = outputs[2] if len(outputs) > 2 else None
        loss = self._seg_loss(da_pred, ll_pred, da_mask, ll_mask, attack_mode)
        return float(loss.item())

    # ------------------------------------------------------------------
    # 3-D stubs (YOLOP handles images only)
    # ------------------------------------------------------------------
    def inference_3d(self, points: np.ndarray) -> Dict[str, Any]:
        raise NotImplementedError("YOLOPModel handles images, not point clouds.")

    def gradients_3d(
        self, points: np.ndarray, gt_boxes: np.ndarray
    ) -> Tuple[np.ndarray, float]:
        raise NotImplementedError("YOLOPModel handles images, not point clouds.")


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

_FAMILY_MAP: Dict[str, Type[BaseModel]] = {
    "openpcdet": OpenPCDetModel,
    "yolop": YOLOPModel,
    "stub": StubModel,
}


class ModelRegistry:
    """Loads and stores model instances keyed by name."""

    def __init__(self) -> None:
        self._models: Dict[str, BaseModel] = {}
        self._model_status: Dict[str, Dict[str, Any]] = {}

    def load_from_yaml(self, config_path: str) -> None:
        """Instantiate all models listed in *config_path*.

        Falls back to ``StubModel`` for any model whose dependencies are
        missing or whose checkpoint file does not exist.

        Args:
            config_path: Path to models.yaml.
        """
        with open(config_path, "r") as fh:
            raw = yaml.safe_load(fh)

        models_raw: dict = raw.get("models", {})
        for name, entry in models_raw.items():
            family = entry.get("family", "stub")
            cfg = ModelConfig(
                name=name,
                family=family,
                device=entry.get("device", "cpu"),
                config_path=entry.get("config_path"),
                checkpoint_path=entry.get("checkpoint_path"),
                extra={k: v for k, v in entry.items()
                       if k not in ("family", "device", "config_path", "checkpoint_path")},
            )
            model, status = self._instantiate(cfg)
            self._models[name] = model
            self._model_status[name] = status

    def _instantiate(self, cfg: ModelConfig) -> Tuple[BaseModel, Dict[str, Any]]:
        status = {
            "name": cfg.name,
            "requested_family": cfg.family,
            "active_family": cfg.family,
            "device": cfg.device,
            "config_path": cfg.config_path,
            "checkpoint_path": cfg.checkpoint_path,
            "is_stub": False,
            "fallback_reason": None,
        }
        cls = _FAMILY_MAP.get(cfg.family)
        if cls is None:
            reason = f"unknown family '{cfg.family}'"
            logger.warning("[ModelRegistry] %s for model '%s'. Using stub.", reason, cfg.name)
            stub_cfg = ModelConfig(name=cfg.name, family="stub", device=cfg.device)
            status.update(active_family="stub", is_stub=True, fallback_reason=reason)
            return StubModel(stub_cfg), status

        ckpt = cfg.checkpoint_path
        if cls is not StubModel and (ckpt is None or not Path(ckpt).exists()):
            reason = f"checkpoint not found: {ckpt}"
            logger.warning("[ModelRegistry] %s for '%s'. Using stub.", reason, cfg.name)
            stub_cfg = ModelConfig(name=cfg.name, family="stub", device=cfg.device)
            status.update(active_family="stub", is_stub=True, fallback_reason=reason)
            return StubModel(stub_cfg), status

        try:
            model = cls(cfg)
            status["active_family"] = cfg.family
            return model, status
        except Exception as exc:
            reason = f"load failed: {exc}"
            logger.warning("[ModelRegistry] Failed to load '%s': %s. Using stub.", cfg.name, exc)
            stub_cfg = ModelConfig(name=cfg.name, family="stub", device=cfg.device)
            status.update(active_family="stub", is_stub=True, fallback_reason=reason)
            return StubModel(stub_cfg), status

    def get(self, name: str) -> BaseModel:
        """Return model by name.

        Args:
            name: Model key as defined in models.yaml.

        Raises:
            KeyError: If the model is not registered.
        """
        if name not in self._models:
            raise KeyError(f"[ModelRegistry] Model '{name}' not registered. "
                           f"Available: {list(self._models)}")
        return self._models[name]

    def list_models(self) -> List[str]:
        """Return sorted list of registered model names."""
        return sorted(self._models)

    def describe_models(self) -> Dict[str, Dict[str, Any]]:
        """Return load status and active implementation for every model."""
        return {name: self._model_status.get(name, {}) for name in self.list_models()}

    def has_unexpected_stubs(self) -> bool:
        """Return True when a model requested as real fell back to stub."""
        return any(
            status.get("is_stub") and status.get("requested_family") != "stub"
            for status in self._model_status.values()
        )


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _to_numpy(x: Any) -> np.ndarray:
    """Convert a tensor or ndarray to a CPU float32/int32 numpy array."""
    if HAS_TORCH:
        import torch
        if isinstance(x, torch.Tensor):
            return x.detach().cpu().numpy()
    if isinstance(x, np.ndarray):
        return x
    return np.array(x)


def _seg_argmax(pred: Any, shape: Tuple[int, int]) -> np.ndarray:
    """Convert segmentation logits to a label map at original resolution."""
    if pred is None:
        return np.zeros(shape, dtype=np.uint8)
    import cv2
    arr = _to_numpy(pred)
    if arr.ndim == 4:
        arr = arr[0]      # (C, H, W)
    label = arr.argmax(axis=0).astype(np.uint8)
    if label.shape != shape:
        label = cv2.resize(label, (shape[1], shape[0]),
                           interpolation=cv2.INTER_NEAREST)
    return label


def load_registry(config_path: str) -> ModelRegistry:
    """Convenience factory: create and populate a ModelRegistry.

    Args:
        config_path: Path to models.yaml.

    Returns:
        Populated ``ModelRegistry`` instance.
    """
    registry = ModelRegistry()
    registry.load_from_yaml(config_path)
    return registry
