"""Vercel serverless entrypoint for the Echo Gate FastAPI gateway.

Vercel's Python runtime discovers ASGI apps exposed as ``app`` in files under
the ``api/`` directory. This module simply re-exports the application defined
in ``app.main`` so the rest of the codebase (and its imports) stay unchanged.
"""

from app.main import app

__all__ = ["app"]
