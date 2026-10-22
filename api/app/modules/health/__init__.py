from app.modules.health.health_controller import (
    root_router as root_health_router,
)
from app.modules.health.health_controller import (
    router as health_router,
)

__all__ = ["health_router", "root_health_router"]
