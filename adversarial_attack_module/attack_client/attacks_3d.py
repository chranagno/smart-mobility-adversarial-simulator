"""
3-D adversarial attacks against LiDAR-based object detectors.

All attacks use the GradientClient to retrieve ∂L_det/∂P from the model
server.  No PyTorch is imported here — only numpy and scipy.

Algorithms
----------
* PointPerturbationAttack — PGD under a per-point L2 constraint.
  Loss: L = −L_det(P_a, G) + λ · Σ_n ||δ_n||²   (Eq. 1 in the paper)

* PointDetachmentAttack   — Saliency-driven greedy point removal.
  Saliency: s_n = ||∇_{p_n} L_det||₂

* PointAttachmentAttack   — Anchor-and-refine: initialise K synthetic points
  at the most salient locations, then refine via PGD.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:
    from .gradient_client import GradientClient
except ImportError:
    from gradient_client import GradientClient

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PointPerturbationConfig:
    """Hyper-parameters for the point perturbation (PGD-L2) attack.

    Attributes:
        epsilon: Per-point L2 ε-ball radius in metres.
        alpha: PGD gradient-ascent step size.
        num_steps: Number of PGD iterations.
        lambda_reg: λ — L2 regularisation weight on the perturbation norm
            (Eq. 1 in the paper).  Larger values penalise large perturbations.
    """

    epsilon: float = 0.10
    alpha: float = 0.01
    num_steps: int = 20
    lambda_reg: float = 0.01


@dataclass
class PointDetachmentConfig:
    """Hyper-parameters for the point detachment (saliency-removal) attack.

    Attributes:
        ratio: Total fraction of points to remove.
        num_iters: Number of greedy removal passes.
        saliency: Gradient reduction used for ranking: ``"l1"`` or ``"l2"``.
        lambda_reg: λ — kept explicit for consistency with Eq. 1 (not used
            for detachment, but recorded in the info dict).
    """

    ratio: float = 0.10
    num_iters: int = 10
    saliency: str = "l1"
    lambda_reg: float = 0.0


@dataclass
class PointAttachmentConfig:
    """Hyper-parameters for the point attachment (anchor-and-refine) attack.

    Attributes:
        K: Number of synthetic points to inject.
        epsilon: Per-point L2 ε-ball radius for the PGD refinement.
        alpha: PGD step size during refinement.
        num_steps: Number of PGD refinement iterations.
        lambda_reg: λ — L2 regularisation weight (Eq. 1).
    """

    K: int = 300
    epsilon: float = 0.05
    alpha: float = 0.005
    num_steps: int = 20
    lambda_reg: float = 0.01


# ─────────────────────────────────────────────────────────────────────────────
# Point Perturbation (PGD-L2)
# ─────────────────────────────────────────────────────────────────────────────

class PointPerturbationAttack:
    """PGD attack under a per-point L2 constraint.

    Maximises the detection loss L_det while penalising large perturbations
    via a regularisation term λ·Σ||δ_n||².

    Args:
        cfg: Attack hyper-parameters.
    """

    def __init__(self, cfg: PointPerturbationConfig | None = None) -> None:
        self.cfg = cfg or PointPerturbationConfig()

    def run(
        self,
        points: np.ndarray,
        model_name: str,
        client: GradientClient,
        gt_boxes: np.ndarray | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run the perturbation attack.

        Args:
            points: Clean (N, 4) float32 point cloud (x, y, z, intensity).
            model_name: Registered model name on the server.
            client: GradientClient connected to the model server.
            gt_boxes: (G, 7) float32 ground-truth boxes, or None.

        Returns:
            Tuple of (adversarial point cloud (N, 4), info dict).
        """
        cfg = self.cfg
        N, F = points.shape
        delta = np.zeros((N, 3), dtype=np.float32)   # perturbation on xyz only
        loss_trace: list[float] = []

        for step in range(cfg.num_steps):
            perturbed = points.copy()
            perturbed[:, :3] += delta

            grad, loss = client.get_3d_gradients(model_name, perturbed, gt_boxes)
            loss_trace.append(loss)
            grad_xyz = grad[:, :3]   # (N, 3)

            # Gradient ascent on L_det + regularisation descent on λ||δ||²
            delta = delta + cfg.alpha * grad_xyz
            delta = delta - cfg.alpha * 2.0 * cfg.lambda_reg * delta

            # Project each point's perturbation onto its ε-ball (L2)
            norms = np.linalg.norm(delta, axis=1, keepdims=True)
            scale = np.minimum(1.0, cfg.epsilon / (norms + 1e-8))
            delta = delta * scale

            logger.debug(
                "[Perturbation] step=%d/%d loss=%.4f max_norm=%.4f",
                step + 1, cfg.num_steps, loss, float(norms.max()),
            )

        adv = points.copy()
        adv[:, :3] += delta
        info = {
            "attack": "perturbation",
            "implementation": "lidar_perturbation",
            "model": model_name,
            "loss_trace": loss_trace,
            "final_loss": loss_trace[-1] if loss_trace else None,
            "max_perturbation_l2": float(np.linalg.norm(delta, axis=1).max()),
            "mean_perturbation_l2": float(np.linalg.norm(delta, axis=1).mean()),
            "config": cfg.__dict__,
        }
        logger.info(
            "[Perturbation] Done. final_loss=%.4f max_l2=%.4f",
            info["final_loss"], info["max_perturbation_l2"],
        )
        return adv, info


# ─────────────────────────────────────────────────────────────────────────────
# Point Detachment (saliency-driven removal)
# ─────────────────────────────────────────────────────────────────────────────

class PointDetachmentAttack:
    """Greedy point removal using the model server as a gradient oracle.

    At each iteration, this client sends only the current raw point cloud to
    the server, receives ∂L/∂P, ranks points by gradient magnitude, and removes
    the most salient points. The client has no model, voxelization, OpenPCDet,
    or environment-specific logic.

    Args:
        cfg: Attack hyper-parameters.
    """

    def __init__(self, cfg: PointDetachmentConfig | None = None) -> None:
        self.cfg = cfg or PointDetachmentConfig()

    def run(
        self,
        points: np.ndarray,
        model_name: str,
        client: GradientClient,
        gt_boxes: np.ndarray | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run the detachment attack.

        Args:
            points: Clean (N, 4) float32 point cloud.
            model_name: Registered model name on the server.
            client: GradientClient connected to the model server.
            gt_boxes: (G, 7) float32 ground-truth boxes, or None.

        Returns:
            Tuple of (adversarial point cloud (M, 4) with M <= N, info dict).
        """
        cfg = self.cfg
        current = points.copy()
        removed_total = 0
        loss_trace: list[float] = []
        ratio = min(max(float(cfg.ratio), 0.0), 1.0)
        num_iters = max(int(cfg.num_iters), 1)
        saliency_mode = str(cfg.saliency).lower()
        if saliency_mode not in ("l1", "l2"):
            raise ValueError("Point detachment saliency must be 'l1' or 'l2'")
        remove_budget = max(0, int(round(ratio * len(current))))
        per_iter = max(1, remove_budget // num_iters) if remove_budget else 0

        for iteration in range(num_iters):
            if len(current) == 0 or removed_total >= remove_budget:
                break

            grad, loss = client.get_3d_gradients(model_name, current, gt_boxes)
            loss_trace.append(loss)

            if saliency_mode == "l1":
                saliency = np.sum(np.abs(grad[:, :3]), axis=1)
            else:
                saliency = np.linalg.norm(grad[:, :3], axis=1)
            remaining_budget = remove_budget - removed_total
            k = min(per_iter, remaining_budget, len(current))
            if k <= 0:
                break
            top_k_idx = np.argsort(saliency)[-k:]

            mask = np.ones(len(current), dtype=bool)
            mask[top_k_idx] = False
            current = current[mask]
            removed_total += k

            logger.debug(
                "[Detachment] iter=%d/%d loss=%.4f removed=%d remaining=%d",
                iteration + 1, cfg.num_iters, loss, k, len(current),
            )

        info = {
            "attack": "detachment",
            "implementation": "lidar_detachment",
            "model": model_name,
            "method": "gradient_oracle_point_removal",
            "saliency": saliency_mode,
            "ratio": ratio,
            "num_iters": num_iters,
            "loss_trace": loss_trace,
            "final_loss": loss_trace[-1] if loss_trace else None,
            "remove_budget": remove_budget,
            "points_removed": removed_total,
            "points_remaining": len(current),
            "config": cfg.__dict__,
        }
        logger.info(
            "[Detachment] Done. final_loss=%.4f removed=%d remaining=%d",
            info["final_loss"], removed_total, len(current),
        )
        return current, info


# ─────────────────────────────────────────────────────────────────────────────
# Point Attachment (anchor-and-refine)
# ─────────────────────────────────────────────────────────────────────────────

class PointAttachmentAttack:
    """Inject synthetic points at salient locations and refine via PGD.

    Step 1 — Anchor: rank all points by saliency, replicate the top-K most
    salient points with small random jitter as initial synthetic points.
    Step 2 — Refine: run PGD on the synthetic points' positions, keeping the
    clean points fixed.

    Args:
        cfg: Attack hyper-parameters.
    """

    def __init__(self, cfg: PointAttachmentConfig | None = None) -> None:
        self.cfg = cfg or PointAttachmentConfig()

    def run(
        self,
        points: np.ndarray,
        model_name: str,
        client: GradientClient,
        gt_boxes: np.ndarray | None = None,
        rng: np.random.Generator | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run the attachment attack.

        Args:
            points: Clean (N, 4) float32 point cloud.
            model_name: Registered model name on the server.
            client: GradientClient connected to the model server.
            gt_boxes: (G, 7) float32 ground-truth boxes, or None.
            rng: Optional numpy random generator for reproducibility.

        Returns:
            Tuple of (augmented point cloud (N+K, 4), info dict).
        """
        cfg = self.cfg
        rng = rng or np.random.default_rng()

        # ── Step 1: Anchor ──
        grad_init, loss_init = client.get_3d_gradients(model_name, points, gt_boxes)
        saliency = np.linalg.norm(grad_init[:, :3], axis=1)
        K = min(cfg.K, len(points))
        top_k_idx = np.argsort(saliency)[-K:]
        synthetic = points[top_k_idx].copy().astype(np.float32)
        # Small random jitter to break exact duplicates
        synthetic[:, :3] += rng.standard_normal((K, 3)).astype(np.float32) * 0.01

        loss_trace: list[float] = [loss_init]
        # Track perturbation of synthetic points relative to their anchors
        anchors = synthetic[:, :3].copy()
        delta = np.zeros((K, 3), dtype=np.float32)

        # ── Step 2: Refine via PGD ──
        for step in range(cfg.num_steps):
            augmented = np.vstack([points, synthetic])
            grad_aug, loss = client.get_3d_gradients(model_name, augmented, gt_boxes)
            loss_trace.append(loss)

            # Only update synthetic points (last K rows)
            grad_syn = grad_aug[-K:, :3]
            delta = delta + cfg.alpha * grad_syn
            delta = delta - cfg.alpha * 2.0 * cfg.lambda_reg * delta
            norms = np.linalg.norm(delta, axis=1, keepdims=True)
            scale = np.minimum(1.0, cfg.epsilon / (norms + 1e-8))
            delta = delta * scale
            synthetic[:, :3] = anchors + delta

            logger.debug(
                "[Attachment] step=%d/%d loss=%.4f max_syn_norm=%.4f",
                step + 1, cfg.num_steps, loss, float(norms.max()),
            )

        adv = np.vstack([points, synthetic])
        info = {
            "attack": "attachment",
            "implementation": "lidar_attachment",
            "model": model_name,
            "loss_trace": loss_trace,
            "final_loss": loss_trace[-1] if loss_trace else None,
            "synthetic_points_added": K,
            "max_synthetic_l2": float(np.linalg.norm(delta, axis=1).max()),
            "config": cfg.__dict__,
        }
        logger.info(
            "[Attachment] Done. final_loss=%.4f injected=%d",
            info["final_loss"], K,
        )
        return adv, info


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def build_3d_attack(attack_type: str, params: dict) -> "PointPerturbationAttack | PointDetachmentAttack | PointAttachmentAttack":
    """Instantiate a 3-D attack from a type string and hyper-parameter dict.

    Args:
        attack_type: ``"perturbation"``, ``"detachment"``, or ``"attachment"``.
        params: Hyper-parameter dict (keys match the dataclass fields).

    Returns:
        Instantiated attack object.

    Raises:
        ValueError: If *attack_type* is not recognised.
    """
    if attack_type == "perturbation":
        cfg = PointPerturbationConfig(**{
            k: params[k] for k in PointPerturbationConfig.__dataclass_fields__
            if k in params
        })
        return PointPerturbationAttack(cfg)
    if attack_type == "detachment":
        cfg = PointDetachmentConfig(**{
            k: params[k] for k in PointDetachmentConfig.__dataclass_fields__
            if k in params
        })
        return PointDetachmentAttack(cfg)
    if attack_type == "attachment":
        cfg = PointAttachmentConfig(**{
            k: params[k] for k in PointAttachmentConfig.__dataclass_fields__
            if k in params
        })
        return PointAttachmentAttack(cfg)
    raise ValueError(
        f"Unknown 3-D attack type '{attack_type}'. "
        "Expected 'perturbation', 'detachment', or 'attachment'."
    )
