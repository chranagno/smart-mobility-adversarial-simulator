"""Print model-server dependency diagnostics.

Run inside the model-server container:

    python model_server/diagnose_imports.py
"""

from __future__ import annotations

import importlib
import os
import sys
import traceback
from pathlib import Path


def check(name: str, import_stmt: str) -> None:
    print(f"\n[{name}] {import_stmt}")
    try:
        exec(import_stmt, {})
    except Exception:
        print("FAIL")
        traceback.print_exc()
    else:
        print("OK")


def main() -> None:
    print("Python:", sys.version)
    print("Executable:", sys.executable)
    print("PYTHONPATH:", os.environ.get("PYTHONPATH", ""))
    print("MODEL_CONFIG:", os.environ.get("MODEL_CONFIG", ""))
    print("cwd:", os.getcwd())

    for path in (
        "/opt/openpcdet",
        "/opt/yolop",
        "/checkpoints/second/second.yaml",
        "/checkpoints/second/second.pth",
        "/checkpoints/pointpillars/pointpillar_custom.yaml",
        "/checkpoints/pointpillars/pointpillar.pth",
        "/checkpoints/yolop/yolop.pth",
    ):
        print(f"path exists {path}: {Path(path).exists()}")

    check("torch", "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())")
    check("pcdet top-level", "import pcdet; print('pcdet module', pcdet.__file__)")
    check("pcdet config", "from pcdet.config import cfg, cfg_from_yaml_file; print('pcdet config OK')")
    check("pcdet models", "from pcdet.models import build_network; print('pcdet models OK')")
    check("spconv", "import spconv; print('spconv', getattr(spconv, '__version__', 'unknown'))")
    check("yolop top-level path", "import lib; print('lib module', lib.__file__)")
    check("yolop model", "from lib.models import get_net; print('YOLOP get_net OK')")

    print("\nInstalled candidates:")
    for module_name in ("pcdet", "spconv", "lib", "torch"):
        spec = importlib.util.find_spec(module_name)
        print(f"{module_name}: {spec.origin if spec else None}")


if __name__ == "__main__":
    main()
