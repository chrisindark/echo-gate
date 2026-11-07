"""Anthropic Messages Protocol Adapter.

Exposes native Anthropic Messages API (POST /api/v1/messages) for direct compatibility
with Claude SDKs, Cursor (Claude mode), and Claude Code.
"""

from app.modules.anthropic.anthropic_controller import anthropic_router

__all__ = ["anthropic_router"]
