# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""A launched application process under the sandbox.

A thin wrapper over subprocess: spawn an argv with a given environment, send its
stdout/stderr to a log file, and tear it down cleanly. App-agnostic — the caller
supplies the argv (e.g. a resolved .app binary path).

An optional `wrapper` prefixes the argv with a launcher — e.g. valgrind, or any
`prog args -- ` style tool — so the app runs under instrumentation without the
caller reassembling the argv. `goldstep.diagnostics` builds common wrappers.
"""

import subprocess


class AppProcess:
    def __init__(self, argv, env, log_path, wrapper=None):
        self.argv = [argv] if isinstance(argv, str) else list(argv)
        # wrapper is a launcher argv prefixed before the app (valgrind, etc.).
        if wrapper:
            self.argv = list(wrapper) + self.argv
        self._env = env
        self._log = open(log_path, "wb")
        self.proc = None
        self.pid = None

    def spawn(self):
        self.proc = subprocess.Popen(self.argv, env=self._env,
                                     stdout=self._log, stderr=self._log)
        self.pid = self.proc.pid
        return self.pid

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def kill(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate(); self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self._log.close()
        except Exception:
            pass
        self.proc = None
