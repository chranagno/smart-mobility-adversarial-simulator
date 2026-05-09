"""
2-D adversarial attacks against semantic segmentation models (YOLOP).

All attacks operate on numpy float32 images normalised to [0, 1].
No PyTorch is imported — only numpy and scipy are used.

Algorithms
----------
* PGDSegmentationAttack — Ioulia PGD under L∞ or L2 constraint.
  * L∞: δ = clip(δ + α·sign(∇_x L), −ε, +ε)
  * L2:  δ = project(δ + α·∇_x L / ||∇_x L||, ε)

* SquareAttack — Ioulia YOLOP segmentation Square Attack.
  Random square patches are accepted when they increase the segmentation
  loss; the run succeeds once adversarial loss reaches/exceeds clean loss.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Literal

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
class PGDConfig:
    """Hyper-parameters for the PGD segmentation attack.

    Attributes:
        norm: Constraint type — ``"linf"`` or ``"l2"``.
        epsilon: Perturbation budget.
            L∞: fraction of [0, 1] range (e.g. ``8/255 ≈ 0.0314``).
            L2: Euclidean radius in pixel space (e.g. ``15.0``).
        alpha: Step size (same units as epsilon).
        num_steps: Number of PGD iterations.
        lambda_reg: λ — regularisation weight (kept explicit per Eq. 1;
            not applied for PGD-L∞ but recorded in the info dict).
    """

    norm: Literal["linf", "l2"] = "linf"
    epsilon: float = 8.0 / 255.0
    alpha: float = 2.0 / 255.0
    num_steps: int = 10
    lambda_reg: float = 0.0
    attack_mode: Literal["seg_drivable", "seg_lane", "seg_both"] = "seg_both"
    targeted: bool = False
    random_start: bool = True


@dataclass
class SquareAttackConfig:
    """Hyper-parameters for the Square Attack.

    Attributes:
        epsilon: L∞ perturbation budget (fraction of [0, 1] range).
        max_queries: Maximum number of model loss queries.
        lambda_reg: λ — kept explicit (not used for black-box attack).
        init_square_frac: Initial square side as a fraction of min(H, W).
    """

    epsilon: float = 8.0 / 255.0
    max_queries: int = 1000
    lambda_reg: float = 0.0
    p_init: float = 0.8
    attack_mode: Literal["seg_drivable", "seg_lane", "seg_both"] = "seg_both"


# ─────────────────────────────────────────────────────────────────────────────
# PGD Segmentation Attack
# ─────────────────────────────────────────────────────────────────────────────

class PGDSegmentationAttack:
    """Ioulia PGD attack on YOLOP segmentation under L∞ or L2 constraint.

    The model-specific YOLOP BCE segmentation loss is computed by the gradient
    server.  This client performs only the input-space update/projection loop
    from ``ioulia_attacks/pgd_only_seg_losses.py``.

    Args:
        cfg: Attack hyper-parameters.
    """

    def __init__(self, cfg: PGDConfig | None = None) -> None:
        self.cfg = cfg or PGDConfig()

    def run(
        self,
        image: np.ndarray,
        model_name: str,
        client: GradientClient,
        da_mask: np.ndarray | None = None,
        ll_mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run the PGD attack.

        Args:
            image: Clean (H, W, 3) float32 image with values in [0, 1].
            model_name: Registered model name on the server.
            client: GradientClient connected to the model server.
            da_mask: Optional drivable-area target mask.
            ll_mask: Optional lane-line target mask.

        Returns:
            Tuple of (adversarial image (H, W, 3) float32, info dict).
        """
        cfg = self.cfg
        adv = image.astype(np.float32).copy()
        clean = image.astype(np.float32).copy()
        direction = -1.0 if cfg.targeted else 1.0

        if cfg.random_start:
            if cfg.norm == "linf":
                adv += np.random.uniform(-cfg.epsilon, cfg.epsilon, adv.shape).astype(np.float32)
            else:
                noise = np.random.randn(*adv.shape).astype(np.float32)
                adv += noise * (cfg.epsilon / (np.linalg.norm(noise) + 1e-8))
            adv = np.clip(adv, 0.0, 1.0)

        loss_trace: list[float] = []

        for step in range(cfg.num_steps):
            grad, loss = client.get_2d_gradients(
                model_name, adv, da_mask=da_mask, ll_mask=ll_mask,
                attack_mode=cfg.attack_mode,
            )
            loss_trace.append(loss)

            if cfg.norm == "linf":
                adv = adv + direction * cfg.alpha * np.sign(grad)
                # Project onto L∞ ε-ball centred on the clean image
                delta = np.clip(adv - clean, -cfg.epsilon, cfg.epsilon)
                adv = np.clip(clean + delta, 0.0, 1.0)
            else:
                # L2 normalised gradient ascent
                gnorm = np.linalg.norm(grad) + 1e-8
                adv = adv + direction * cfg.alpha * grad / gnorm
                delta = adv - clean
                d_norm = np.linalg.norm(delta)
                if d_norm > cfg.epsilon:
                    delta = delta * (cfg.epsilon / d_norm)
                adv = np.clip(clean + delta, 0.0, 1.0)

            logger.debug(
                "[PGD-%s] step=%d/%d loss=%.4f",
                cfg.norm.upper(), step + 1, cfg.num_steps, loss,
            )

        final_delta = adv - clean
        info = {
            "attack": "pgd",
            "implementation": "ioulia_seg_pgd",
            "norm": cfg.norm,
            "model": model_name,
            "attack_mode": cfg.attack_mode,
            "targeted": cfg.targeted,
            "has_da_mask": da_mask is not None,
            "has_ll_mask": ll_mask is not None,
            "loss_trace": loss_trace,
            "final_loss": loss_trace[-1] if loss_trace else None,
            "max_delta_linf": float(np.abs(final_delta).max()),
            "delta_l2": float(np.linalg.norm(final_delta)),
            "config": cfg.__dict__,
        }
        logger.info(
            "[PGD-%s] Done. final_loss=%.4f max_Δ_L∞=%.5f Δ_L2=%.4f",
            cfg.norm.upper(), info["final_loss"],
            info["max_delta_linf"], info["delta_l2"],
        )
        return adv, info


# ─────────────────────────────────────────────────────────────────────────────
# Square Attack
# ─────────────────────────────────────────────────────────────────────────────

class SquareAttack:
    """Ioulia black-box Square Attack on YOLOP segmentation.

    This ports the query schedule and clean-loss margin criterion from
    ``ioulia_attacks/seg_square.py`` while keeping model/loss computation on
    the server. No gradient information is requested.

    Reference: Andriushchenko et al., ECCV 2020.

    Args:
        cfg: Attack hyper-parameters.
    """

    def __init__(self, cfg: SquareAttackConfig | None = None) -> None:
        self.cfg = cfg or SquareAttackConfig()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _p_selection(it: int, n_queries: int, p_init: float) -> float:
        """Ioulia/AutoAttack square-size schedule."""
        if n_queries < 10000:
            it = int(it / max(n_queries, 1) * 10000)
        if 10 < it <= 50:
            return p_init / 2
        if 50 < it <= 200:
            return p_init / 4
        if 200 < it <= 500:
            return p_init / 8
        if 500 < it <= 1000:
            return p_init / 16
        if 1000 < it <= 2000:
            return p_init / 32
        if 2000 < it <= 4000:
            return p_init / 64
        if 4000 < it <= 6000:
            return p_init / 128
        if 6000 < it <= 8000:
            return p_init / 256
        if it > 8000:
            return p_init / 512
        return p_init

    def run(
        self,
        image: np.ndarray,
        model_name: str,
        client: GradientClient,
        da_mask: np.ndarray | None = None,
        ll_mask: np.ndarray | None = None,
        rng: np.random.Generator | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run the Square Attack.

        Args:
            image: Clean (H, W, 3) float32 image with values in [0, 1].
            model_name: Registered model name on the server.
            client: GradientClient connected to the model server.
            da_mask: Optional drivable-area target mask.
            ll_mask: Optional lane-line target mask.
            rng: Optional numpy random generator for reproducibility.

        Returns:
            Tuple of (adversarial image (H, W, 3) float32, info dict).
        """
        cfg = self.cfg
        rng = rng or np.random.default_rng()
        clean = image.astype(np.float32).copy()
        H, W = image.shape[:2]
        C = 3
        n_features = C * H * W
        eps = cfg.epsilon

        clean_loss = client.get_2d_query_loss(
            model_name, clean, da_mask=da_mask, ll_mask=ll_mask,
            attack_mode=cfg.attack_mode,
        )

        # Ioulia initialisation: random +/- eps vertical signs across width.
        random_sign = rng.choice([-1.0, 1.0], size=(1, W, C)).astype(np.float32)
        adv = np.clip(clean + eps * random_sign, 0.0, 1.0)
        current_loss = client.get_2d_query_loss(
            model_name, adv, da_mask=da_mask, ll_mask=ll_mask,
            attack_mode=cfg.attack_mode,
        )
        n_queries = 2
        accepted = 0
        loss_trace: list[float] = [current_loss]
        success = current_loss >= clean_loss

        while n_queries < cfg.max_queries and not success:
            p = self._p_selection(n_queries, cfg.max_queries, cfg.p_init)
            side = max(int(round(math.sqrt(p * n_features / C))), 1)
            side = min(side, H, W)
            r = int(rng.integers(0, H - side + 1))
            c = int(rng.integers(0, W - side + 1))

            candidate = adv.copy()
            patch_sign = rng.choice([-1.0, 1.0], size=(1, 1, C)).astype(np.float32)
            candidate[r:r + side, c:c + side, :] += 2.0 * eps * patch_sign
            candidate = np.maximum(np.minimum(candidate, clean + eps), clean - eps)
            candidate = np.clip(candidate, 0.0, 1.0)

            candidate_loss = client.get_2d_query_loss(
                model_name, candidate, da_mask=da_mask, ll_mask=ll_mask,
                attack_mode=cfg.attack_mode,
            )
            n_queries += 1

            crossed = candidate_loss >= clean_loss
            if candidate_loss > current_loss or crossed:
                adv = candidate
                current_loss = candidate_loss
                accepted += 1
                loss_trace.append(current_loss)
                success = crossed

            if n_queries % 100 == 0:
                logger.debug(
                    "[Square] queries=%d loss=%.4f accepted=%d",
                    n_queries, current_loss, accepted,
                )

        info = {
            "attack": "square",
            "implementation": "ioulia_seg_square",
            "model": model_name,
            "attack_mode": cfg.attack_mode,
            "has_da_mask": da_mask is not None,
            "has_ll_mask": ll_mask is not None,
            "clean_loss": clean_loss,
            "queries_used": n_queries,
            "accepted_patches": accepted,
            "success": success,
            "final_loss": current_loss,
            "loss_trace": loss_trace,
            "max_delta_linf": float(np.abs(adv - clean).max()),
            "config": cfg.__dict__,
        }
        logger.info(
            "[Square] Done. queries=%d accepted=%d final_loss=%.4f",
            n_queries, accepted, current_loss,
        )
        return adv, info


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def build_2d_attack(
    attack_type: str, params: dict
) -> "PGDSegmentationAttack | SquareAttack":
    """Instantiate a 2-D attack from a type string and hyper-parameter dict.

    Args:
        attack_type: ``"pgd"`` or ``"square"``.
        params: Hyper-parameter dict (keys match the dataclass fields).

    Returns:
        Instantiated attack object.

    Raises:
        ValueError: If *attack_type* is not recognised.
    """
    if attack_type == "pgd":
        cfg = PGDConfig(**{
            k: params[k] for k in PGDConfig.__dataclass_fields__
            if k in params
        })
        return PGDSegmentationAttack(cfg)
    if attack_type == "square":
        cfg = SquareAttackConfig(**{
            k: params[k] for k in SquareAttackConfig.__dataclass_fields__
            if k in params
        })
        return SquareAttack(cfg)
    raise ValueError(
        f"Unknown 2-D attack type '{attack_type}'. Expected 'pgd' or 'square'."
    )
