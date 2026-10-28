"""Process-level settings: which lock file, which roles, which device.

Everything here is environment-driven because the same image runs on a laptop,
a VM, and a Kubernetes pod. Defaults are chosen so that `uv run inference-platform`
with no environment at all is a valid (if minimal) server: it reads the lock that
sits next to this package and loads every role the lock declares.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("inference.settings")

# Wire contract version. Bump only for breaking changes; additive request/response
# fields (for example `sparse`) do not move this number, because consumers that
# predate the field must keep working unchanged.
CONTRACT_VERSION = "1"

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent

MAX_DOCUMENTS = 256


def lock_path() -> Path:
    """Where `models.lock.yaml` lives.

    Order: explicit `INFERENCE_LOCK_PATH`, then the repo checkout this package was
    imported from, then the process working directory (the container case, where
    the lock is mounted next to the app).
    """
    override = os.getenv("INFERENCE_LOCK_PATH", "").strip()
    if override:
        return Path(override).expanduser()

    checkout = PROJECT_ROOT / "models.lock.yaml"
    if checkout.is_file():
        return checkout
    return Path.cwd() / "models.lock.yaml"


def load_lock(path: Path | None = None) -> dict[str, Any]:
    resolved = path or lock_path()
    spec = yaml.safe_load(resolved.read_text()) or {}
    if not isinstance(spec, dict) or not spec.get("roles"):
        raise RuntimeError(f"{resolved} has no `roles` mapping")
    return spec


def declared_roles(spec: dict[str, Any]) -> list[str]:
    return list((spec.get("roles") or {}).keys())


def wanted_roles(spec: dict[str, Any]) -> list[str]:
    """Roles to load.

    Default is *every* role in the lock, so a consumer never discovers a missing
    role at request time: it either loads or `/health` says so. Trimming via
    `INFERENCE_ROLES` is for a 16 GB laptop that only exercises the cache path.
    """
    raw = os.getenv("INFERENCE_ROLES", "").strip()
    if not raw:
        return declared_roles(spec)
    return [name.strip() for name in raw.split(",") if name.strip()]


def model_cache_dir() -> str:
    """Optional single cache root for downloaded weights.

    Empty by default, which leaves Hugging Face and fastembed on their own
    defaults (`~/.cache/...`). Set it in a container or on a VM to put every weight
    on one volume, so a pod restart re-reads a disk instead of the network.
    """
    return os.getenv("INFERENCE_MODEL_CACHE", "").strip()


def prepare_cache_env() -> None:
    """Point the model libraries at the shared cache, if one was configured.

    Called before any model loads. Deliberately a no-op when
    `INFERENCE_MODEL_CACHE` is unset: silently relocating the HF cache would
    re-download every weight on an existing machine.
    """
    cache = model_cache_dir()
    if not cache:
        return
    Path(cache).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", cache)


def resolve_device() -> str:
    """auto -> mps when available, else cpu.

    MPS is a wash for the sub-100M cross-encoders at batch 1 and a real win when a
    caller batches heavily, so it is the better default but never forced.
    """
    requested = os.getenv("INFERENCE_DEVICE", "auto").strip().lower()
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except (
        Exception
    ):  # pragma: no cover - torch always present via sentence-transformers
        logger.debug("torch MPS probe failed; using cpu", exc_info=True)
    return "cpu"
