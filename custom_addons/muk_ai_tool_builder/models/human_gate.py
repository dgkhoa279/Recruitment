"""Unforgeable in-process marker: MCP arguments cannot clear a ContextVar."""
from contextvars import ContextVar

AI_DISPATCH = ContextVar('muk_ai_tool_builder_dispatch', default=False)
