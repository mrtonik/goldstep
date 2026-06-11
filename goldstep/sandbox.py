# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""A throwaway GNUstep user-defaults sandbox.

Each session runs the app under a private GNUSTEP_USER_DEFAULTS_DIR so the real
desktop and the user's preferences are never touched. The system GlobalDefaults
are seeded in (so the theme + system prefs still load), an optional theme bundle
can be forced via GSTheme, and a sandbox-local GNUstep.conf redirects the user
defaults/config dirs into the temp tree. Paths come from a Config, so nothing
host-specific is hardcoded.

This is app-agnostic; app instrumentation (e.g. a state-dump) injects its own env
via add_env() rather than the sandbox knowing about it.
"""

import os
import plistlib
import shutil
import tempfile


class Sandbox:
    def __init__(self, config, theme=None, extra_defaults=None):
        self.config = config
        self.dir = tempfile.mkdtemp(prefix="goldstep-")
        self.defaults_dir = os.path.join(self.dir, "Defaults")
        os.makedirs(self.defaults_dir, exist_ok=True)
        self._extra_env = {}
        self._seed_global_defaults()
        theme = theme if theme is not None else config.theme
        if theme:
            self._set_theme(theme)
        if extra_defaults:
            self._merge_global_domain(extra_defaults)
        self.conf = os.path.join(self.dir, "GNUstep.conf")
        self._write_conf()

    def _seed_global_defaults(self):
        src = self.config.global_defaults_dir
        if not src or not os.path.isdir(src):
            return
        for name in os.listdir(src):
            if name.endswith(".plist"):
                shutil.copy(os.path.join(src, name),
                            os.path.join(self.defaults_dir, name))

    def _set_theme(self, theme_path):
        """Point GSTheme at an absolute bundle path so the sandboxed app loads a
        specific (e.g. dev) theme build instead of the installed one."""
        path = os.path.join(self.defaults_dir, "NSGlobalDomain.plist")
        try:
            with open(path, "rb") as f:
                data = plistlib.load(f)
        except Exception:
            data = {}
        data["GSTheme"] = theme_path
        with open(path, "wb") as f:
            plistlib.dump(data, f)

    def _merge_global_domain(self, extra):
        """Merge extra keys into NSGlobalDomain (e.g. NSMenuInterfaceStyle) so a
        test can pick a self-hosted menu style — an isolated sandbox has no
        external menu server to host a Mac-style global bar."""
        path = os.path.join(self.defaults_dir, "NSGlobalDomain.plist")
        try:
            with open(path, "rb") as f:
                data = plistlib.load(f)
        except Exception:
            data = {}
        data.update(extra)
        with open(path, "wb") as f:
            plistlib.dump(data, f)

    def _write_conf(self):
        """Sandbox-local GNUstep.conf: inherit the system conf (minus its user
        dir lines) and redirect the user defaults/config dirs into the temp tree.
        If no system conf was found, synthesize a minimal one."""
        lines = []
        if self.config.system_conf:
            try:
                with open(self.config.system_conf) as f:
                    lines = f.readlines()
            except OSError:
                lines = []
        drop = ("GNUSTEP_USER_DEFAULTS_DIR", "GNUSTEP_USER_CONFIG_FILE")
        kept = [ln for ln in lines if ln.split("=", 1)[0].strip() not in drop]
        kept.append("GNUSTEP_USER_DEFAULTS_DIR=%s\n" % self.defaults_dir)
        kept.append("GNUSTEP_USER_CONFIG_FILE=%s\n" % os.path.join(self.dir, "user-none.conf"))
        with open(self.conf, "w") as f:
            f.writelines(kept)

    def add_env(self, mapping):
        """Register extra environment to carry into launched apps (used by opt-in
        instrumentation such as a state-dump). Merged by env()."""
        self._extra_env.update(mapping)

    def env(self):
        e = dict(os.environ)
        e["GNUSTEP_CONFIG_FILE"] = self.conf
        e.update(self._extra_env)
        return e

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)
