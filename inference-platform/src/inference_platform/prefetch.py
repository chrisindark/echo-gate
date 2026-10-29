"""Download every role's weights without serving a single request.

Two uses:

- ``--build-arg PREFETCH=1 docker build ...`` bakes the weights into the image, so
  a rollout does not spend its first minute downloading 700 MB to a disk.
- A Kubernetes init container that fills a model volume before the server starts,
  so the first request after a cold node does not pay for the download either.

    uv run inference-platform-prefetch
    INFERENCE_ROLES=embed,sparse INFERENCE_MODEL_CACHE=/models \
      uv run inference-platform-prefetch
"""

from __future__ import annotations

import logging
import sys

from inference_platform.roles import Registry
from inference_platform.settings import (
    load_lock,
    model_cache_dir,
    prepare_cache_env,
    resolve_device,
    wanted_roles,
)

logger = logging.getLogger("inference.prefetch")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    # fastembed/huggingface log every HTTP HEAD per shard; it buries the result.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    prepare_cache_env()
    spec = load_lock()
    roles = wanted_roles(spec)
    device = resolve_device()
    logger.info(
        "prefetching %s onto %s (cache=%s)",
        roles,
        device,
        model_cache_dir() or "library default",
    )

    registry = Registry()
    registry.load(spec, roles, device)

    for name in roles:
        role = registry.roles.get(name)
        if role is not None:
            print(f"ok      {name:10s} {role.model_id}")
    for name, reason in sorted(registry.failed.items()):
        print(f"FAILED  {name:10s} {reason}", file=sys.stderr)

    if registry.failed:
        print(
            "\nsome roles could not be fetched; the server will report them as "
            "unloaded and /health will stay degraded",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
