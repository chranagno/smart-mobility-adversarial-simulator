"""
Attack Orchestrator — entry point for the simulator.

Reads attack campaign configuration from the scenario YAML and dispatches
3-D (LiDAR) and 2-D (RGB) adversarial attacks via the REST-backed attacks.

Usage (from the simulator)::

    from attack_client import AttackOrchestrator

    orch = AttackOrchestrator.from_config(scenario_params)
    adv_pc, info = orch.run_3d_attack(
        "perturbation", "second", points, gt_boxes=gt
    )
    adv_img, info = orch.run_2d_attack(
        "pgd", "yolop", image
    )
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import yaml

try:
    from .attacks_2d import build_2d_attack
    from .attacks_3d import build_3d_attack
    from .gradient_client import GradientClient
except ImportError:
    from attacks_2d import build_2d_attack
    from attacks_3d import build_3d_attack
    from gradient_client import GradientClient

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AttackOrchestratorConfig:
    """Top-level configuration for the AttackOrchestrator.

    Attributes:
        server_url: URL of the model gradient server.
        timeout_s: Per-request timeout for the HTTP client.
        attacks_3d: List of 3-D attack campaign entries (from YAML).
        attacks_2d: List of 2-D attack campaign entries (from YAML).
        lidar_sensor_id: Sensor slot id used to look up LiDAR data.
        camera_sensor_id: Sensor slot id used to look up RGB data.
        save_adversarial: Write adversarial outputs to disk alongside clean.
        save_clean: Also write the clean copy for comparison.
        output_subdir: Subfolder name under the recorder base directory.
    """

    server_url: str = "http://localhost:8100"
    timeout_s: float = 30.0
    attacks_3d: list[dict] = field(default_factory=list)
    attacks_2d: list[dict] = field(default_factory=list)
    lidar_sensor_id: str = "lidar_top"
    camera_sensor_id: str = "rgb_front"
    save_adversarial: bool = True
    save_clean: bool = True
    output_subdir: str = "adversarial"

    @classmethod
    def from_dict(cls, cfg: dict) -> "AttackOrchestratorConfig":
        """Construct from a nested dict (e.g. loaded from scenario YAML).

        Args:
            cfg: ``adversarial_attack`` section of the scenario YAML.

        Returns:
            Populated ``AttackOrchestratorConfig``.
        """
        server = cfg.get("server", {})
        targets = cfg.get("sensor_targets", {})
        output = cfg.get("output", {})
        return cls(
            server_url=server.get("url", "http://localhost:8100"),
            timeout_s=float(server.get("timeout_s", 30.0)),
            attacks_3d=[e for e in cfg.get("attacks_3d", [])
                        if e.get("enabled", True)],
            attacks_2d=[e for e in cfg.get("attacks_2d", [])
                        if e.get("enabled", True)],
            lidar_sensor_id=targets.get("lidar_sensor_id", "lidar_top"),
            camera_sensor_id=targets.get("camera_sensor_id", "rgb_front"),
            save_adversarial=bool(output.get("save_adversarial", True)),
            save_clean=bool(output.get("save_clean", True)),
            output_subdir=output.get("subdir", "adversarial"),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "AttackOrchestratorConfig":
        """Load campaign config from a YAML file.

        Args:
            path: Path to ``attack_scenario_sample.yaml`` or similar.

        Returns:
            Populated ``AttackOrchestratorConfig``.
        """
        with open(path, "r") as fh:
            raw = yaml.safe_load(fh)
        return cls.from_dict(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

class AttackOrchestrator:
    """Dispatches adversarial attacks and manages the HTTP client lifecycle.

    The orchestrator is constructed once at simulator start-up and its
    ``run_3d_attack`` / ``run_2d_attack`` methods are called from within
    the simulation step loop.

    Args:
        cfg: Orchestrator configuration.
    """

    def __init__(self, cfg: AttackOrchestratorConfig) -> None:
        self.cfg = cfg
        self._client = GradientClient(
            server_url=cfg.server_url,
            timeout=cfg.timeout_s,
        )
        self._enabled = False
        self._check_server()

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, scenario_params: dict) -> "AttackOrchestrator | None":
        """Construct from a merged scenario YAML dict.

        Returns None if the ``adversarial_attack`` section is absent or
        ``enabled: false``.

        Args:
            scenario_params: Top-level scenario dict (merged general + scenario).

        Returns:
            AttackOrchestrator instance, or None.
        """
        raw = scenario_params.get("adversarial_attack")
        if not raw:
            return None
        if not raw.get("enabled", True):
            logger.info("[AttackOrchestrator] Attack module disabled in config.")
            return None
        cfg = AttackOrchestratorConfig.from_dict(raw)
        return cls(cfg)

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "AttackOrchestrator":
        """Construct from a standalone campaign YAML file.

        Args:
            yaml_path: Path to an attack scenario YAML.

        Returns:
            AttackOrchestrator instance.
        """
        cfg = AttackOrchestratorConfig.from_yaml(yaml_path)
        return cls(cfg)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_server(self) -> None:
        """Verify connectivity to the model server."""
        ok = self._client.health_check()
        if ok:
            models = self._client.list_models()
            self._enabled = True
            logger.info(
                "[AttackOrchestrator] Server reachable at %s. Models: %s",
                self.cfg.server_url, models,
            )
        else:
            self._enabled = False
            logger.warning(
                "[AttackOrchestrator] Cannot reach model server at %s. "
                "Attacks will be skipped until server is available.",
                self.cfg.server_url,
            )

    def is_enabled(self) -> bool:
        """Return True if the server is reachable and attacks can run.

        Returns:
            bool
        """
        return self._enabled

    def refresh_server_status(self) -> bool:
        """Re-check model server connectivity.

        Returns:
            True when the server is reachable and attacks can run.
        """
        self._check_server()
        return self._enabled

    def _mark_server_unavailable(self, exc: Exception) -> None:
        """Disable attacks after a transport failure until the next refresh."""
        if isinstance(exc, ConnectionError):
            self._enabled = False
            logger.warning(
                "[AttackOrchestrator] Lost connection to model server at %s. "
                "Attacks will pause until the next server refresh.",
                self.cfg.server_url,
            )

    # ------------------------------------------------------------------
    # 3-D attack dispatch
    # ------------------------------------------------------------------

    def run_3d_attack(
        self,
        attack_type: str,
        model_name: str,
        points: np.ndarray,
        gt_boxes: np.ndarray | None = None,
        **kwargs: Any,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run a named 3-D attack and return the adversarial point cloud.

        Args:
            attack_type: ``"perturbation"``, ``"detachment"``, or ``"attachment"``.
            model_name: Registered model name (e.g. ``"second"``).
            points: Clean (N, 4) float32 point cloud.
            gt_boxes: (G, 7) float32 ground-truth boxes, or None.
            **kwargs: Override any config dataclass field (e.g. ``epsilon=0.05``).

        Returns:
            Tuple of (adversarial point cloud, info dict).
        """
        if not self._enabled:
            logger.debug("[AttackOrchestrator] Server not available, returning clean points.")
            return points, {"skipped": True, "reason": "server_unavailable"}

        t0 = time.perf_counter()
        try:
            attack = build_3d_attack(attack_type, kwargs)
            adv, info = attack.run(
                points=points,
                model_name=model_name,
                client=self._client,
                gt_boxes=gt_boxes,
            )
        except Exception as exc:
            self._mark_server_unavailable(exc)
            logger.exception("[AttackOrchestrator] 3-D attack '%s' failed: %s", attack_type, exc)
            return points, {"error": str(exc)}

        info["wall_time_s"] = time.perf_counter() - t0
        return adv, info

    def run_3d_attack_from_entry(
        self,
        entry: dict,
        points: np.ndarray,
        gt_boxes: np.ndarray | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run a 3-D attack from a campaign YAML entry dict.

        Args:
            entry: Single entry from ``attacks_3d`` list in the YAML.
            points: Clean (N, 4) float32 point cloud.
            gt_boxes: (G, 7) float32 ground-truth boxes, or None.

        Returns:
            Tuple of (adversarial point cloud, info dict).
        """
        attack_type = entry["type"]
        model_name = entry["model"]
        params = {k: v for k, v in entry.items()
                  if k not in ("type", "model", "name", "enabled")}
        return self.run_3d_attack(attack_type, model_name, points, gt_boxes, **params)

    # ------------------------------------------------------------------
    # 2-D attack dispatch
    # ------------------------------------------------------------------

    def run_2d_attack(
        self,
        attack_type: str,
        model_name: str,
        image: np.ndarray,
        da_mask: np.ndarray | None = None,
        ll_mask: np.ndarray | None = None,
        **kwargs: Any,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run a named 2-D attack and return the adversarial image.

        Args:
            attack_type: ``"pgd"`` or ``"square"``.
            model_name: Registered model name (e.g. ``"yolop"``).
            image: Clean (H, W, 3) float32 image with values in [0, 1].
            da_mask: Optional drivable-area target mask for Ioulia segmentation losses.
            ll_mask: Optional lane-line target mask for Ioulia segmentation losses.
            **kwargs: Override any config dataclass field.

        Returns:
            Tuple of (adversarial image (H, W, 3) float32, info dict).
        """
        if not self._enabled:
            logger.debug("[AttackOrchestrator] Server not available, returning clean image.")
            return image, {"skipped": True, "reason": "server_unavailable"}

        t0 = time.perf_counter()
        try:
            attack = build_2d_attack(attack_type, kwargs)
            adv, info = attack.run(
                image=image,
                model_name=model_name,
                client=self._client,
                da_mask=da_mask,
                ll_mask=ll_mask,
            )
        except Exception as exc:
            self._mark_server_unavailable(exc)
            logger.exception("[AttackOrchestrator] 2-D attack '%s' failed: %s", attack_type, exc)
            return image, {"error": str(exc)}

        info["wall_time_s"] = time.perf_counter() - t0
        return adv, info

    def run_2d_attack_from_entry(
        self,
        entry: dict,
        image: np.ndarray,
        da_mask: np.ndarray | None = None,
        ll_mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Run a 2-D attack from a campaign YAML entry dict.

        Args:
            entry: Single entry from ``attacks_2d`` list in the YAML.
            image: Clean (H, W, 3) float32 image with values in [0, 1].
            da_mask: Optional drivable-area target mask.
            ll_mask: Optional lane-line target mask.

        Returns:
            Tuple of (adversarial image, info dict).
        """
        attack_type = entry["type"]
        model_name = entry["model"]
        params = {k: v for k, v in entry.items()
                  if k not in ("type", "model", "name", "enabled")}
        return self.run_2d_attack(
            attack_type, model_name, image,
            da_mask=da_mask, ll_mask=ll_mask, **params,
        )

    # ------------------------------------------------------------------
    # Batch run — all enabled attacks for a single frame
    # ------------------------------------------------------------------

    def run_all_3d(
        self,
        points: np.ndarray,
        gt_boxes: np.ndarray | None = None,
    ) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Run every enabled 3-D attack entry in sequence.

        Each attack's output is fed as input to the next (chained), so the
        final adversarial point cloud reflects all enabled attacks combined.

        Args:
            points: Clean (N, 4) float32 point cloud.
            gt_boxes: (G, 7) float32 ground-truth boxes, or None.

        Returns:
            Tuple of (final adversarial point cloud, list of info dicts).
        """
        current = points
        infos: list[dict] = []
        for entry in self.cfg.attacks_3d:
            current, info = self.run_3d_attack_from_entry(entry, current, gt_boxes)
            info["entry_name"] = entry.get("name", entry.get("type"))
            info["_adversarial_points"] = np.asarray(current, dtype=np.float32)
            infos.append(info)
        return current, infos

    def run_all_2d(
        self,
        image: np.ndarray,
        da_mask: np.ndarray | None = None,
        ll_mask: np.ndarray | None = None,
    ) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Run every enabled 2-D attack entry in sequence (chained).

        Args:
            image: Clean (H, W, 3) float32 image with values in [0, 1].
            da_mask: Optional drivable-area target mask.
            ll_mask: Optional lane-line target mask.

        Returns:
            Tuple of (final adversarial image, list of info dicts).
        """
        current = image
        infos: list[dict] = []
        for entry in self.cfg.attacks_2d:
            current, info = self.run_2d_attack_from_entry(
                entry, current, da_mask=da_mask, ll_mask=ll_mask,
            )
            info["entry_name"] = entry.get("name", entry.get("type"))
            infos.append(info)
        return current, infos
