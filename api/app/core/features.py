"""Feature-flag registry for deployment modes and optional surfaces.

The OSS build ships exactly one mode, ``oss``. A cloud (or enterprise)
overlay — a separate pip package or code overlay installed only into the
cloud image — registers the ``cloud`` mode and any cloud-only routes
(e.g. ``/api/v1/billing/*``) when the ee package is present. This module
is the seams' registry: it contains **no license logic**.

Usage:
    - Gateway code asks ``features.is_enabled("billing")``.
    - The ee overlay calls ``features.register_cloud()`` at import/boot,
      which flips the registry and registers any cloud routers.
    - Route registration in ``app/main.py`` checks ``features.mode`` /
      ``features.is_enabled`` so the OSS image never mounts cloud routes.
"""

from __future__ import annotations

import os

# Deployment modes. OSS is the only mode the open-source image ever runs;
# ``DEPLOYMENT_MODE=cloud`` is set by the cloud overlay, not by the OSS
# build, and simply resolves to "oss" until the overlay registers itself.
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "oss").strip().lower() or "oss"

_FEATURES: set[str] = set()
_CLOUD_FEATURES: tuple[str, ...] = ("billing", "metering_sink", "event_bus")


def register_cloud() -> None:
    """Called by the ee overlay to activate cloud features (no-op in OSS)."""
    _FEATURES.update(_CLOUD_FEATURES)


def is_enabled(feature: str) -> bool:
    """True if a feature flag is active (billing is cloud-only)."""
    if feature in ("billing", "metering_sink", "event_bus"):
        return "billing" in _FEATURES
    # Unknown flags default to off — fail-closed for anything cloud-only.
    return feature in _FEATURES


def mode() -> str:
    """The active deployment mode (``oss`` in this build)."""
    return "cloud" if "billing" in _FEATURES else "oss"


def enabled_features() -> frozenset[str]:
    """Snapshot of every currently-enabled feature (for /health and tests)."""
    return frozenset(_FEATURES)
