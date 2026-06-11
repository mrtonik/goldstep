# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""A nested Xephyr X server, used as a dedicated/frontmost display for visual
tests.

Why this exists: on a shared host display the app is never the frontmost X
client, and GNUstep hides NSPanels (and flips key-window state) when its app is
not active. That makes panel/sheet/drawer/menu/popup/alert chrome impossible to
screenshot deterministically. Inside a private Xephyr the app is the ONLY client,
so it stays active: panels stay up and key state is stable, which unlocks that
chrome.

No window manager is started: GNUstep draws its own window decoration, and with a
single client there is nothing to arbitrate. Capture from INSIDE the nested
server (`import -display :N`), never the host, so the host desktop and cursor
never leak into a golden.
"""

import os
import subprocess
import time


def _free_display(lo=10, hi=64):
    for n in range(lo, hi):
        if not os.path.exists("/tmp/.X11-unix/X%d" % n):
            return n
    raise RuntimeError("no free X display in :%d..:%d" % (lo, hi))


class Xephyr:
    """Context manager: a running Xephyr on a private display.

    with Xephyr(1280, 900) as xeph:
        env_display = xeph.display          # e.g. ":11"
        ... launch app with DISPLAY=env_display ...
    """

    def __init__(self, width=1280, height=900, dpi=96, log_path=None):
        self.width = width
        self.height = height
        self.dpi = dpi
        self.num = None
        self.display = None
        self.proc = None
        self._log_path = log_path
        self._log = None

    def start(self, ready_timeout=15):
        self.num = _free_display()
        self.display = ":%d" % self.num
        self._log = open(self._log_path, "wb") if self._log_path else subprocess.DEVNULL
        # -ac: no access control (same-user import/xdotool); -br: black root;
        # -no-host-grab: never grab the host kbd/pointer (don't lock the desktop);
        # -screen fixes the framebuffer size we golden against.
        argv = ["Xephyr", self.display,
                "-screen", "%dx%d" % (self.width, self.height),
                "-ac", "-br", "-no-host-grab", "-nolisten", "tcp",
                "-dpi", str(self.dpi)]
        self.proc = subprocess.Popen(argv, stdout=self._log, stderr=self._log)
        self._await_ready(ready_timeout)
        return self

    def _await_ready(self, timeout):
        sock = "/tmp/.X11-unix/X%d" % self.num
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("Xephyr %s exited rc=%s" % (self.display, self.proc.returncode))
            if os.path.exists(sock):
                r = subprocess.run(["xdpyinfo", "-display", self.display],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if r.returncode == 0:
                    return
            time.sleep(0.2)
        raise RuntimeError("Xephyr %s not ready in %ss" % (self.display, timeout))

    def stop(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate(); self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        if self._log not in (None, subprocess.DEVNULL):
            try:
                self._log.close()
            except Exception:
                pass
        self.proc = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
