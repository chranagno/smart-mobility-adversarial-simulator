"""
HTTP client for the Model Gradient Server.

Serialises numpy arrays as flat float lists for transport and deserialises
responses back into numpy arrays.  No PyTorch dependency — only numpy and
requests are required on the simulator side.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class GradientClient:
    """Thin HTTP client around the model gradient server REST API.

    Args:
        server_url: Base URL of the model server (e.g. ``http://localhost:8100``).
        timeout: Per-request timeout in seconds.
        max_retries: Number of automatic retries on connection errors.
    """

    def __init__(
        self,
        server_url: str = "http://localhost:8100",
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self._base = server_url.rstrip("/")
        self._timeout = timeout
        self._session = self._build_session(max_retries)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_session(max_retries: int) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=0.3,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods={"POST", "GET"},
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._base}{path}"
        t0 = time.perf_counter()
        try:
            resp = self._session.post(url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ConnectionError(
                f"[GradientClient] POST {url} failed: {exc}"
            ) from exc
        latency = (time.perf_counter() - t0) * 1000.0
        logger.debug("[GradientClient] POST %s → %d ms", path, latency)
        return resp.json()

    def _get(self, path: str) -> dict:
        url = f"{self._base}{path}"
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ConnectionError(
                f"[GradientClient] GET {url} failed: {exc}"
            ) from exc
        return resp.json()

    @staticmethod
    def _points_payload(
        model: str, points: np.ndarray, num_features: int | None = None
    ) -> dict:
        pts = np.asarray(points, dtype=np.float32)
        nf = pts.shape[1] if pts.ndim == 2 else (num_features or 4)
        return {
            "model": model,
            "points": pts.flatten().tolist(),
            "num_features": nf,
        }

    @staticmethod
    def _image_payload(
        model: str,
        image: np.ndarray,
        da_mask: np.ndarray | None = None,
        ll_mask: np.ndarray | None = None,
        attack_mode: str = "seg_both",
    ) -> dict:
        img = np.asarray(image, dtype=np.float32)
        assert img.ndim == 3 and img.shape[2] == 3, \
            "image must be (H, W, 3) float32"
        H, W = img.shape[:2]
        payload = {
            "model": model,
            "image": img.flatten().tolist(),
            "height": H,
            "width": W,
            "attack_mode": attack_mode,
        }
        if da_mask is not None:
            da = np.asarray(da_mask, dtype=np.float32)
            payload["da_mask"] = da.flatten().tolist()
            payload["mask_height"] = int(da.shape[0])
            payload["mask_width"] = int(da.shape[1])
        if ll_mask is not None:
            ll = np.asarray(ll_mask, dtype=np.float32)
            payload["ll_mask"] = ll.flatten().tolist()
            payload.setdefault("mask_height", int(ll.shape[0]))
            payload.setdefault("mask_width", int(ll.shape[1]))
        return payload

    # ------------------------------------------------------------------
    # Meta endpoints
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """Return True if the server responds to /health.

        Returns:
            True on success, False on connection error.
        """
        try:
            resp = self._get("/health")
            return resp.get("status") in {"ok", "degraded"}
        except ConnectionError:
            return False

    def list_models(self) -> list[str]:
        """Return the list of model names available on the server.

        Returns:
            Sorted list of model name strings.
        """
        resp = self._get("/api/v1/models")
        return resp.get("models", [])

    # ------------------------------------------------------------------
    # 3-D endpoints
    # ------------------------------------------------------------------

    def get_3d_inference(
        self, model: str, points: np.ndarray
    ) -> dict[str, np.ndarray]:
        """Run 3-D detection forward pass.

        Args:
            model: Registered model name (e.g. ``"second"``).
            points: Float32 array of shape (N, 4) — x, y, z, intensity.

        Returns:
            Dict with keys ``"boxes"`` (M, 7), ``"scores"`` (M,), ``"labels"`` (M,).
        """
        payload = self._points_payload(model, points)
        resp = self._post("/api/v1/3d/inference", payload)
        return {
            "boxes": np.array(resp["boxes"], dtype=np.float32),
            "scores": np.array(resp["scores"], dtype=np.float32),
            "labels": np.array(resp["labels"], dtype=np.int32),
        }

    def get_3d_gradients(
        self,
        model: str,
        points: np.ndarray,
        gt_boxes: np.ndarray | None = None,
    ) -> tuple[np.ndarray, float]:
        """Compute ∂L_det/∂P.

        Args:
            model: Registered model name.
            points: (N, 4) float32 point cloud.
            gt_boxes: (G, 7) float32 ground-truth boxes, or None.

        Returns:
            Tuple of (gradient array (N, 4) float32, scalar loss float).
        """
        payload = self._points_payload(model, points)
        if gt_boxes is not None:
            gt = np.asarray(gt_boxes, dtype=np.float32)
            payload["gt_boxes"] = gt.flatten().tolist()
            payload["num_box_features"] = gt.shape[1] if gt.ndim == 2 else 7
        resp = self._post("/api/v1/3d/gradients", payload)
        N, F = len(points), points.shape[1] if points.ndim == 2 else 4
        grad = np.array(resp["gradients"], dtype=np.float32).reshape(N, F)
        return grad, float(resp["loss"])

    # ------------------------------------------------------------------
    # 2-D endpoints
    # ------------------------------------------------------------------

    def get_2d_inference(
        self, model: str, image: np.ndarray
    ) -> dict[str, np.ndarray]:
        """Run 2-D segmentation forward pass.

        Args:
            model: Registered model name (e.g. ``"yolop"``).
            image: (H, W, 3) float32 image normalised to [0, 1].

        Returns:
            Dict with keys ``"drivable"`` (H, W) and ``"lanes"`` (H, W) uint8 masks.
        """
        H, W = image.shape[:2]
        payload = self._image_payload(model, image)
        resp = self._post("/api/v1/2d/inference", payload)
        return {
            "drivable": np.array(resp["drivable"], dtype=np.uint8).reshape(H, W),
            "lanes": np.array(resp["lanes"], dtype=np.uint8).reshape(H, W),
        }

    def get_2d_gradients(
        self,
        model: str,
        image: np.ndarray,
        da_mask: np.ndarray | None = None,
        ll_mask: np.ndarray | None = None,
        attack_mode: str = "seg_both",
    ) -> tuple[np.ndarray, float]:
        """Compute ∂L_seg/∂x.

        Args:
            model: Registered model name.
            image: (H, W, 3) float32 image normalised to [0, 1].
            da_mask: Optional drivable-area target mask (H, W), 0/1.
            ll_mask: Optional lane-line target mask (H, W), 0/1.
            attack_mode: ``"seg_drivable"``, ``"seg_lane"``, or ``"seg_both"``.

        Returns:
            Tuple of (gradient array (H, W, 3) float32, scalar loss float).
        """
        H, W = image.shape[:2]
        payload = self._image_payload(model, image, da_mask, ll_mask, attack_mode)
        resp = self._post("/api/v1/2d/gradients", payload)
        grad = np.array(resp["gradients"], dtype=np.float32).reshape(H, W, 3)
        return grad, float(resp["loss"])

    def get_2d_query_loss(
        self,
        model: str,
        image: np.ndarray,
        da_mask: np.ndarray | None = None,
        ll_mask: np.ndarray | None = None,
        attack_mode: str = "seg_both",
    ) -> float:
        """Return scalar segmentation loss without computing gradients.

        Used by the black-box Square Attack.

        Args:
            model: Registered model name.
            image: (H, W, 3) float32 image normalised to [0, 1].
            da_mask: Optional drivable-area target mask (H, W), 0/1.
            ll_mask: Optional lane-line target mask (H, W), 0/1.
            attack_mode: ``"seg_drivable"``, ``"seg_lane"``, or ``"seg_both"``.

        Returns:
            Scalar loss value.
        """
        payload = self._image_payload(model, image, da_mask, ll_mask, attack_mode)
        resp = self._post("/api/v1/2d/query_loss", payload)
        return float(resp["loss"])
