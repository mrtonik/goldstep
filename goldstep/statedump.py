# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""Optional app-side instrumentation: a JSON state-dump reader.

UIBridge's live tree exposes structure + basic state, but not everything a UI
test sometimes wants (cell state, first-responder, default-ness, exact fonts).
The pattern here: instrument the app to periodically write a JSON snapshot of its
widgets into the sandbox, and read it back as ground truth. This is entirely
opt-in — Bridge works without it (tree-only). It's a plugin so the core stays
app-agnostic.

The app writes to $<dir_env>/<name><suffix> every <interval> seconds (the app
reads <dir_env>/<interval_env> from its environment). The env key names + file
suffix are parameters so an existing instrumentation contract can be matched
without changing the app.
"""

import json
import os
import time

from .mcp import MCPError


class StateDump:
    def __init__(self, sandbox, name, *, dir_env="GOLDSTEP_DUMP_DIR",
                 interval_env="GOLDSTEP_DUMP_INTERVAL", interval="0.3",
                 suffix=".state.json"):
        self.sandbox = sandbox
        self.name = name
        self.dir_env = dir_env
        self.interval_env = interval_env
        self.interval = str(interval)
        self.suffix = suffix

    def instrument(self):
        """Register the env the instrumented app reads, into the sandbox. Call
        before launch so the app starts dumping into the sandbox dir."""
        self.sandbox.add_env({self.dir_env: self.sandbox.dir,
                              self.interval_env: self.interval})

    def path(self):
        return os.path.join(self.sandbox.dir, "%s%s" % (self.name, self.suffix))

    def exists(self):
        return os.path.exists(self.path())

    def read(self, after=None, timeout=4):
        """Latest dump. If `after` (epoch) is given, wait for a dump written at or
        after that time (the app re-dumps every `interval`)."""
        path = self.path()
        deadline = time.time() + timeout
        while True:
            try:
                mtime = os.path.getmtime(path)
                if after is None or mtime >= after - 0.05:
                    with open(path) as f:
                        return json.load(f)
            except (OSError, ValueError):
                pass
            if time.time() > deadline:
                raise MCPError("no fresh dump at %s" % path)
            time.sleep(0.1)

    @staticmethod
    def widget(dump, identifier=None, tag=None):
        for w in dump.get("widgets", []):
            if identifier is not None and w.get("identifier") == identifier:
                return w
            if tag is not None and w.get("tag") == tag:
                return w
        return None
