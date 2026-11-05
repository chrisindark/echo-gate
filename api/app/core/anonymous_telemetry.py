"""Opt-in anonymous usage telemetry (version + instance ping).

Off by default (``TELEMETRY_ENABLED=false``). When enabled *and* a
``TELEMETRY_ENDPOINT`` is configured, the gateway POSTs to that URL on
startup and then every ``TELEMETRY_INTERVAL_SECONDS`` (default: daily)
with only:

    {"version", "instance_id", "deployment_mode", "event": "startup"|"heartbeat"}

The instance id is an anonymous random UUID (``TELEMETRY_INSTANCE_ID`` or a
freshly generated one for this process). No prompts, responses, tenant
data, API keys, or client IPs are ever sent. All failures are logged and
swallowed — telemetry can never break or slow down request serving.
"""

import asyncio
import logging
import uuid
from pathlib import Path

import httpx

from app.core.config import config

logger = logging.getLogger(__name__)

# Instance ids are random UUIDs, not derived from any host attribute, so an
# operator can wipe them by restarting with a fresh TELEMETRY_INSTANCE_ID.
_INSTANCE_ID_PATH: Path | None = None


def _resolve_instance_id() -> str:
    """Return the configured instance id, or a fresh random UUID.

    A persistent on-disk id is deliberately NOT used: one random UUID per
    process lifetime is enough to count distinct deployments in an
    opt-in cohort without creating a tracking identifier.
    """
    configured = (getattr(config, "TELEMETRY_INSTANCE_ID", "") or "").strip()
    if configured:
        return configured
    return str(uuid.uuid4())


async def _ping_anonymous_usage(event: str) -> None:
    """Send one anonymous ping. Fail-open: never raises."""
    endpoint = (getattr(config, "TELEMETRY_ENDPOINT", "") or "").strip()
    if not endpoint:
        logger.debug(
            "TELEMETRY_ENABLED=true but TELEMETRY_ENDPOINT is unset; "
            "skipping anonymous usage ping."
        )
        return
    payload = {
        "version": getattr(config, "VERSION", "unknown"),
        "instance_id": _resolve_instance_id(),
        "deployment_mode": getattr(config, "TELEMETRY_DEPLOYMENT_MODE", "self-hosted"),
        "event": event,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(endpoint, json=payload)
            if resp.status_code >= 400:
                logger.debug(
                    "Anonymous telemetry ping returned %s (ignored).",
                    resp.status_code,
                )
    except Exception as exc:  # fail-open by design
        logger.debug("Anonymous telemetry ping failed (ignored): %s", exc)


async def run_anonymous_telemetry_loop() -> None:
    """Startup ping + periodic heartbeat loop. Runs only when enabled."""
    if not getattr(config, "TELEMETRY_ENABLED", False):
        return
    interval = max(60, int(getattr(config, "TELEMETRY_INTERVAL_SECONDS", 86400)))
    logger.info(
        "Anonymous usage telemetry enabled (endpoint set): pinging on "
        "startup, then every %ss.",
        interval,
    )
    while True:
        try:
            await _ping_anonymous_usage("startup" if interval else "heartbeat")
        except Exception:  # pragma: no cover - _ping_* is already fail-open
            logger.debug("Anonymous telemetry loop iteration failed", exc_info=True)
        await asyncio.sleep(interval)


def schedule_anonymous_telemetry() -> asyncio.Task | None:
    """Schedule the telemetry loop as a background task. Fail-open.

    Returns the task (so the lifespan can cancel it on shutdown), or None
    when telemetry is disabled. Never raises.
    """
    if not getattr(config, "TELEMETRY_ENABLED", False):
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # called outside a running loop (tests)
        return None
    return loop.create_task(run_anonymous_telemetry_loop())
