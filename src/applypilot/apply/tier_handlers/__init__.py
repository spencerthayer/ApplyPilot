"""Tier handlers — T0 through T7 apply strategies."""

from applypilot.apply.tier_handlers.registry import dispatch, get_handler

__all__ = ["dispatch", "get_handler"]
