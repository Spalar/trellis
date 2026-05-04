"""Trellis - Python-native code graph workflow layer.

Built on top of code-graph-mcp for core graph capabilities,
with added features for spec validation, team workflows, and visual exploration.
"""

from .bridge import CodeGraphBridge
from .api import app

__version__ = "0.2.0"
__all__ = ["CodeGraphBridge", "app"]
