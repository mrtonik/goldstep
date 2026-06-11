# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""Environment / installation discovery for goldstep.

Resolves the few host-specific things the harness needs — the UIBridge server
binary, the GNUstep system config + GlobalDefaults to inherit, an optional theme
to force, and a Python interpreter — from (in order) an explicit env var, a
best-effort discovery, then a fallback chain. Designed to work unchanged on
Gershwin / FreeBSD (paths under /System) and on a generic GNUstep install on
Linux. Everything is overridable so nothing about a particular host is baked in.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

# System GNUstep.conf candidates, in priority order. The first existing one is
# inherited (its non-user lines are copied into the sandbox conf). Gershwin keeps
# it under /System; stock GNUstep installs vary.
_CONF_CANDIDATES = (
    "/System/Library/Preferences/GNUstep.conf",
    "/usr/GNUstep/System/Library/Preferences/GNUstep.conf",
    "/usr/local/share/GNUstep/GNUstep.conf",
    "/etc/GNUstep/GNUstep.conf",
)
_SERVER_FALLBACK = "~/Library/Tools/UIBridgeServer"


def _gnustep_config(var):
    """Ask `gnustep-config --variable=VAR`; None if the tool/var is unavailable."""
    try:
        out = subprocess.run(["gnustep-config", "--variable=%s" % var],
                             capture_output=True, text=True)
    except OSError:
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def find_uibridge_server():
    """Path to the UIBridgeServer binary: $UIBRIDGE_SERVER, else $PATH, else the
    conventional ~/Library/Tools location (existence is checked by the caller)."""
    return (os.environ.get("UIBRIDGE_SERVER")
            or shutil.which("UIBridgeServer")
            or os.path.expanduser(_SERVER_FALLBACK))


def find_system_conf():
    """The system GNUstep.conf to inherit, or None if none is found."""
    env = os.environ.get("GNUSTEP_CONFIG_FILE")
    if env and os.path.isfile(env):
        return env
    for c in _CONF_CANDIDATES:
        if os.path.isfile(c):
            return c
    return None


def find_global_defaults_dir(system_conf=None):
    """The GlobalDefaults dir whose *.plist seed the sandbox (so the theme +
    system prefs load), or None. Looked for next to the system conf and under the
    GNUstep system library."""
    env = os.environ.get("GOLDSTEP_GLOBAL_DEFAULTS")
    if env and os.path.isdir(env):
        return env
    candidates = []
    if system_conf:
        candidates.append(os.path.join(os.path.dirname(system_conf), "GlobalDefaults"))
    sysdir = _gnustep_config("GNUSTEP_SYSTEM_LIBRARY")
    if sysdir:
        candidates.append(os.path.join(sysdir, "Preferences", "GlobalDefaults"))
    candidates.append("/System/Library/Preferences/GlobalDefaults")
    for d in candidates:
        if d and os.path.isdir(d):
            return d
    return None


def find_python():
    return os.environ.get("PYTHON") or shutil.which("python3") or sys.executable


@dataclass
class Config:
    """Resolved host configuration. Build with Config.load(); override any field
    by constructing it directly."""

    uibridge_server: str
    system_conf: str | None
    global_defaults_dir: str | None
    theme: str | None
    python: str

    @classmethod
    def load(cls, theme=None):
        conf = find_system_conf()
        return cls(
            uibridge_server=find_uibridge_server(),
            system_conf=conf,
            global_defaults_dir=find_global_defaults_dir(conf),
            theme=(theme if theme is not None
                   else (os.environ.get("GOLDSTEP_THEME") or None)),
            python=find_python(),
        )
