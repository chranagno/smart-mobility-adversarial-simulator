"""
Model Gradient Server — FastAPI application.

Exposes REST endpoints for inference and gradient computation so that the
attack client (simulator side) can run adversarial attacks without importing
PyTorch or OpenPCDet.

Start:
    MODEL_CONFIG=../configs/models.yaml python server.py

Environment variables:
    MODEL_CONFIG   path to models.yaml (required)
    HOST           bind host (default 0.0.0.0)
    PORT           bind port (default 8100)
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple, Type

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel as PydanticBase, Field

from model_registry import ModelRegistry, load_registry

logger = logging.getLogger("model_server")

# ─────────────────────────────────────────────────────────────────────────────
# Global registry (populated on startup)
# ─────────────────────────────────────────────────────────────────────────────

registry: Optional[ModelRegistry] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry
    config_path = os.environ.get("MODEL_CONFIG", "configs/models.yaml")
    logger.info("[Server] Loading model registry from: %s", config_path)
    registry = load_registry(config_path)
    logger.info("[Server] Models loaded: %s", registry.list_models())
    yield
    logger.info("[Server] Shutting down.")


app = FastAPI(
    title="Model Gradient Server",
    version="1.0.0",
    description=(
        "REST endpoints for 3-D (LiDAR) and 2-D (RGB) model inference and "
        "gradient computation. Supports SECOND, PointPillars, CenterPoint, YOLOP."
    ),
    lifespan=lifespan,
)

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic request / response schemas
# ─────────────────────────────────────────────────────────────────────────────

class Inference3dRequest(PydanticBase):
    model: str = Field(..., description="Registered model name (e.g. 'second')")
    points: List[float] = Field(
        ..., description="Flat list of floats — reshaped to (N, 4): x,y,z,intensity"
    )
    num_features: int = Field(4, description="Point feature dimension (usually 4)")


class Inference3dResponse(PydanticBase):
    model: str
    boxes: List[List[float]]    # M × 7
    scores: List[float]         # M
    labels: List[int]           # M
    latency_ms: float


class Gradient3dRequest(PydanticBase):
    model: str
    points: List[float]          # flat (N × num_features)
    num_features: int = 4
    gt_boxes: List[float] = Field(
        default_factory=list,
        description="Flat list — reshaped to (G, 7): x,y,z,dx,dy,dz,yaw"
    )
    num_box_features: int = Field(7, description="Box feature dimension (usually 7)")


class Gradient3dResponse(PydanticBase):
    model: str
    gradients: List[float]       # flat (N × num_features)
    loss: float
    latency_ms: float


class Inference2dRequest(PydanticBase):
    model: str
    image: List[float]           # flat (H × W × 3), values in [0, 1]
    height: int
    width: int


class Inference2dResponse(PydanticBase):
    model: str
    drivable: List[int]          # flat (H × W) uint8
    lanes: List[int]             # flat (H × W) uint8
    latency_ms: float


class Gradient2dRequest(PydanticBase):
    model: str
    image: List[float]           # flat (H × W × 3), values in [0, 1]
    height: int
    width: int
    da_mask: List[float] = Field(default_factory=list)
    ll_mask: List[float] = Field(default_factory=list)
    mask_height: Optional[int] = None
    mask_width: Optional[int] = None
    attack_mode: str = Field(
        "seg_both",
        description="'seg_drivable', 'seg_lane', or 'seg_both'",
    )


class Gradient2dResponse(PydanticBase):
    model: str
    gradients: List[float]       # flat (H × W × 3)
    loss: float
    latency_ms: float


class QueryLoss2dRequest(PydanticBase):
    model: str
    image: List[float]
    height: int
    width: int
    da_mask: List[float] = Field(default_factory=list)
    ll_mask: List[float] = Field(default_factory=list)
    mask_height: Optional[int] = None
    mask_width: Optional[int] = None
    attack_mode: str = "seg_both"


class QueryLoss2dResponse(PydanticBase):
    model: str
    loss: float
    latency_ms: float


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_registry() -> ModelRegistry:
    if registry is None:
        raise HTTPException(status_code=503, detail="Model registry not initialised.")
    return registry


def _flat_to_points(flat: List[float], num_features: int) -> np.ndarray:
    arr = np.array(flat, dtype=np.float32)
    if arr.size % num_features != 0:
        raise HTTPException(
            status_code=422,
            detail=f"points length {arr.size} is not divisible by num_features={num_features}",
        )
    return arr.reshape(-1, num_features)


def _flat_to_image(flat: List[float], H: int, W: int) -> np.ndarray:
    arr = np.array(flat, dtype=np.float32)
    expected = H * W * 3
    if arr.size != expected:
        raise HTTPException(
            status_code=422,
            detail=f"image length {arr.size} != {H}*{W}*3={expected}",
        )
    return arr.reshape(H, W, 3)


def _flat_to_mask(flat: List[float], H: Optional[int], W: Optional[int]) -> Optional[np.ndarray]:
    if not flat:
        return None
    if H is None or W is None:
        raise HTTPException(status_code=422, detail="mask_height/mask_width are required with masks")
    arr = np.array(flat, dtype=np.float32)
    expected = H * W
    if arr.size != expected:
        raise HTTPException(
            status_code=422,
            detail=f"mask length {arr.size} != {H}*{W}={expected}",
        )
    return arr.reshape(H, W)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
async def health() -> Dict[str, Any]:
    """Server liveness check."""
    r = _require_registry()
    status = "degraded" if r.has_unexpected_stubs() else "ok"
    return {
        "status": status,
        "models": r.list_models(),
        "model_status": r.describe_models(),
        "timestamp": time.time(),
    }


@app.get("/api/v1/models", tags=["meta"])
async def list_models() -> Dict[str, Any]:
    """List all registered model names."""
    r = _require_registry()
    return {"models": r.list_models(), "details": r.describe_models()}


# ── 3-D endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/v1/3d/inference", response_model=Inference3dResponse, tags=["3d"])
async def inference_3d(req: Inference3dRequest) -> Inference3dResponse:
    """Run 3-D detection forward pass and return predicted boxes."""
    r = _require_registry()
    try:
        model = r.get(req.model)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    points = _flat_to_points(req.points, req.num_features)
    t0 = time.perf_counter()
    try:
        out = model.inference_3d(points)
    except NotImplementedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("[Server] inference_3d failed for model '%s'", req.model)
        raise HTTPException(status_code=500, detail=str(exc))

    latency = (time.perf_counter() - t0) * 1000.0
    return Inference3dResponse(
        model=req.model,
        boxes=out["boxes"].tolist(),
        scores=out["scores"].tolist(),
        labels=out["labels"].tolist(),
        latency_ms=latency,
    )


@app.post("/api/v1/3d/gradients", response_model=Gradient3dResponse, tags=["3d"])
async def gradients_3d(req: Gradient3dRequest) -> Gradient3dResponse:
    """Compute ∂L_det/∂P — gradient of detection loss w.r.t. the point cloud."""
    r = _require_registry()
    try:
        model = r.get(req.model)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    points = _flat_to_points(req.points, req.num_features)

    if req.gt_boxes:
        gt = np.array(req.gt_boxes, dtype=np.float32).reshape(-1, req.num_box_features)
    else:
        gt = np.zeros((0, req.num_box_features), dtype=np.float32)

    t0 = time.perf_counter()
    try:
        grad, loss = model.gradients_3d(points, gt)
    except NotImplementedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("[Server] gradients_3d failed for model '%s'", req.model)
        raise HTTPException(status_code=500, detail=str(exc))

    latency = (time.perf_counter() - t0) * 1000.0
    return Gradient3dResponse(
        model=req.model,
        gradients=grad.flatten().tolist(),
        loss=float(loss),
        latency_ms=latency,
    )


# ── 2-D endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/v1/2d/inference", response_model=Inference2dResponse, tags=["2d"])
async def inference_2d(req: Inference2dRequest) -> Inference2dResponse:
    """Run 2-D segmentation forward pass and return drivable/lane masks."""
    r = _require_registry()
    try:
        model = r.get(req.model)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    image = _flat_to_image(req.image, req.height, req.width)
    t0 = time.perf_counter()
    try:
        out = model.inference_2d(image)
    except NotImplementedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("[Server] inference_2d failed for model '%s'", req.model)
        raise HTTPException(status_code=500, detail=str(exc))

    latency = (time.perf_counter() - t0) * 1000.0
    return Inference2dResponse(
        model=req.model,
        drivable=out["drivable"].flatten().tolist(),
        lanes=out["lanes"].flatten().tolist(),
        latency_ms=latency,
    )


@app.post("/api/v1/2d/gradients", response_model=Gradient2dResponse, tags=["2d"])
async def gradients_2d(req: Gradient2dRequest) -> Gradient2dResponse:
    """Compute ∂L_seg/∂x — gradient of segmentation loss w.r.t. the image."""
    r = _require_registry()
    try:
        model = r.get(req.model)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    image = _flat_to_image(req.image, req.height, req.width)
    da_mask = _flat_to_mask(req.da_mask, req.mask_height, req.mask_width)
    ll_mask = _flat_to_mask(req.ll_mask, req.mask_height, req.mask_width)
    t0 = time.perf_counter()
    try:
        grad, loss = model.gradients_2d(
            image, da_mask=da_mask, ll_mask=ll_mask, attack_mode=req.attack_mode
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("[Server] gradients_2d failed for model '%s'", req.model)
        raise HTTPException(status_code=500, detail=str(exc))

    latency = (time.perf_counter() - t0) * 1000.0
    return Gradient2dResponse(
        model=req.model,
        gradients=grad.flatten().tolist(),
        loss=float(loss),
        latency_ms=latency,
    )


@app.post("/api/v1/2d/query_loss", response_model=QueryLoss2dResponse, tags=["2d"])
async def query_loss_2d(req: QueryLoss2dRequest) -> QueryLoss2dResponse:
    """Return scalar segmentation loss (no gradient) — used by Square Attack."""
    r = _require_registry()
    try:
        model = r.get(req.model)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    image = _flat_to_image(req.image, req.height, req.width)
    da_mask = _flat_to_mask(req.da_mask, req.mask_height, req.mask_width)
    ll_mask = _flat_to_mask(req.ll_mask, req.mask_height, req.mask_width)
    t0 = time.perf_counter()
    try:
        loss = model.query_loss_2d(
            image, da_mask=da_mask, ll_mask=ll_mask, attack_mode=req.attack_mode
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("[Server] query_loss_2d failed for model '%s'", req.model)
        raise HTTPException(status_code=500, detail=str(exc))

    latency = (time.perf_counter() - t0) * 1000.0
    return QueryLoss2dResponse(model=req.model, loss=float(loss), latency_ms=latency)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s  %(message)s",
    )
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8100"))
    uvicorn.run(app, host=host, port=port, log_level="info")
