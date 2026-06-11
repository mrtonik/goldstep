# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""Session: sandbox an app, launch it, attach over UIBridge, wait until ready.

A context manager that ties the pieces together:

    with Session("/path/to/Foo.app/Foo") as s:
        s.bridge.find(cls="NSButton")
        ...

`launch` is a binary path, an argv list, or a callable returning one. Optional
app-side instrumentation (a state-dump) is wired via `state_dump` (a callable
taking the Sandbox, e.g. statedump.StateDump). run()/run_xephyr() wrap a Session
in a TAP test, the latter inside a private Xephyr.
"""

import os
import time
import traceback

from .bridge import Bridge
from .config import Config
from .mcp import MCPClient, MCPError
from .process import AppProcess
from .sandbox import Sandbox
from .tap import Test


def _resolve_launch(launch):
    if callable(launch):
        launch = launch()
    return [launch] if isinstance(launch, str) else list(launch)


def _default_name(launch):
    argv = _resolve_launch(launch)
    return os.path.basename(argv[0])


class Session:
    def __init__(self, launch, name=None, *, ready_timeout=20, keep=False,
                 display=None, extra_defaults=None, theme=None, config=None,
                 state_dump=None, require_dump=False, bridge_cls=Bridge):
        self.launch = launch
        self.name = name or _default_name(launch)
        self.ready_timeout = ready_timeout
        self.keep = keep or bool(os.environ.get("GOLDSTEP_KEEP"))
        # display=None => host $DISPLAY; ":N" => a private Xephyr. When set, both
        # the app AND the UIBridge server are pinned to it, so the app is the
        # only/frontmost client (panels stay up, key state stable).
        self.display = display
        self.extra_defaults = extra_defaults
        self.theme = theme
        self.config = config
        self.state_dump_factory = state_dump
        self.require_dump = require_dump
        self.bridge_cls = bridge_cls
        self.sandbox = None
        self.app = None
        self.client = None
        self.bridge = None
        self.dump = None

    def __enter__(self):
        self.config = self.config or Config.load(theme=self.theme)
        self.sandbox = Sandbox(self.config, theme=self.theme,
                               extra_defaults=self.extra_defaults)
        if callable(self.state_dump_factory):
            self.dump = self.state_dump_factory(self.sandbox)
        elif self.state_dump_factory:
            self.dump = self.state_dump_factory
        if self.dump:
            self.dump.instrument()

        server_env = None
        if self.display:
            server_env = dict(os.environ); server_env["DISPLAY"] = self.display
        self.client = MCPClient(
            [self.config.uibridge_server], env=server_env,
            stderr_path=os.path.join(self.sandbox.dir, "uibridge.log")).start()
        self.bridge = self.bridge_cls(self.client, display=self.display)
        self.bridge.state_dump = self.dump

        app_env = self.sandbox.env()
        if self.display:
            app_env["DISPLAY"] = self.display
        log = os.path.join(self.sandbox.dir, "app.log")
        self.app = AppProcess(_resolve_launch(self.launch), app_env, log)
        pid = self.app.spawn()
        self._await_ready(pid)
        return self

    def _await_ready(self, pid):
        deadline = time.time() + self.ready_timeout
        last = None
        while time.time() < deadline:
            if not self.app.alive():
                raise MCPError("%s exited during startup (see %s)" %
                               (self.name, self.sandbox.dir))
            try:
                self.bridge.attach(pid)
                root = self.bridge.get_root()
                if root.get("NSApp") and any(not w.get("hidden")
                                             for w in root.get("windows", [])):
                    if not self.require_dump or (self.dump and self.dump.exists()):
                        self.bridge.pid = pid
                        return
            except MCPError as e:
                last = e
            time.sleep(0.4)
        raise MCPError("%s not ready in %ss (%s)" % (self.name, self.ready_timeout, last))

    def __exit__(self, *exc):
        if self.app:
            self.app.kill()
        if self.client:
            self.client.close()
        if self.sandbox and not self.keep:
            self.sandbox.cleanup()
        elif self.sandbox:
            import sys
            sys.stderr.write("# kept sandbox: %s\n" % self.sandbox.dir)
        return False


def run(name, launch, body, *, app_name=None, display=None, **session_kw):
    """Run body(t, session) as a TAP test named `name`, against a Session over
    `launch`. `app_name` labels the Session (logs/dump); extra keyword args pass
    through to Session."""
    t = Test(name)
    try:
        with Session(launch, name=app_name, display=display, **session_kw) as s:
            body(t, s)
    except SystemExit:
        raise
    except Exception:
        t.fail("session/setup", traceback.format_exc())
    t.done()


def run_xephyr(name, launch, body, *, app_name=None, size=(1280, 900), **session_kw):
    """Like run(), but inside a private Xephyr so the app is the frontmost client
    (panels/sheets/menus stay up). body(t, session, xephyr) also gets the Xephyr
    (its .display feeds screenshot.capture(..., display=...))."""
    from .xephyr import Xephyr
    t = Test(name)
    try:
        with Xephyr(size[0], size[1]) as xeph:
            with Session(launch, name=app_name, display=xeph.display, **session_kw) as s:
                body(t, s, xeph)
    except SystemExit:
        raise
    except Exception:
        t.fail("session/setup", traceback.format_exc())
    t.done()
