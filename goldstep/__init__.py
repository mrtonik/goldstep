# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""goldstep — a golden-image UI test harness for GNUstep applications.

Drives a real GNUstep app through the UIBridge MCP server (widget tree, menus,
XTEST mouse/keyboard) under a throwaway defaults sandbox, optionally inside a
private Xephyr, and asserts on structure, the live tree, and per-window
golden-image diffs. Emits TAP. No AI involved — MCP here is just JSON-RPC over
stdio.

Quick start:

    from goldstep import run

    def body(t, s):
        t.ok(s.bridge.find(cls="NSButton"), "has a button")

    run("smoke", "/path/to/Foo.app/Foo", body)
"""

from . import diagnostics
from . import screenshot
from .bridge import Bridge
from .config import Config
from .mcp import MCPClient, MCPError
from .process import AppProcess
from .sandbox import Sandbox
from .session import Session, run, run_xephyr
from .statedump import StateDump
from .tap import Test
from .xephyr import Xephyr

__all__ = [
    "MCPClient", "MCPError", "Config", "Sandbox", "AppProcess", "Bridge",
    "Session", "run", "run_xephyr", "Xephyr", "Test", "StateDump", "screenshot",
    "diagnostics",
]
