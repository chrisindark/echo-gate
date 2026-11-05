"""Cloud-only billing router. Mounted from app/main.py only when
features.is_enabled("billing") (i.e. the ee overlay registered itself).

The OSS build never imports this module: ``app/main.py`` checks the
feature registry before importing, so there is no billing surface in the
open-source image. When the cloud overlay registers cloud features, this
router provides /api/v1/billing/* backed by the cloud's Stripe metering.
"""

from fastapi import APIRouter

billing_router = APIRouter(
    prefix="/api/v1/billing", tags=["billing"], include_in_schema=False
)

# The cloud overlay (full implementation in the private ee repo) registers
# real handlers here. The seam itself is the two registrations below:
# features.is_enabled("billing") gates the router, and this module exists
# so the import in app/main.py has a stable target.
