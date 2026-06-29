"""MCP-style tool server boundary (v2.3).

The model/router may propose an action, but this package is the controlled
execution surface: a typed request enters, the registry validates the tool, the
server checks caller scope + broker authorization + action envelope, and only
then a scoped handler runs.
"""

from __future__ import annotations

from .schema import ToolAuditRecord, ToolError, ToolRequest, ToolResponse, ToolStatus
from .server import ToolServer, default_tool_server

__all__ = [
    "ToolAuditRecord",
    "ToolError",
    "ToolRequest",
    "ToolResponse",
    "ToolServer",
    "ToolStatus",
    "default_tool_server",
]
