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
import subprocess
import tempfile


def _defaults_tool():
    """The GNUstep `defaults` CLI: $GOLDSTEP_DEFAULTS_TOOL, else $PATH, else the
    conventional /System location."""
    return (os.environ.get("GOLDSTEP_DEFAULTS_TOOL")
            or shutil.which("defaults")
            or "/System/Library/Tools/defaults")


class Sandbox:
    def __init__(self, config, theme=None, extra_defaults=None):
        self.config = config
        self.dir = tempfile.mkdtemp(prefix="goldstep-")
        self.defaults_dir = os.path.join(self.dir, "Defaults")
        os.makedirs(self.defaults_dir, exist_ok=True)
        self._extra_env = {}
        self._seed_global_defaults()
        # theme: a bundle path forces GSTheme to it; None inherits config.theme;
        # False forces the *base* GSTheme by removing any inherited GSTheme key
        # (the seeded system defaults may carry one) — used for differential
        # "Eau-vs-base" control runs.
        theme = theme if theme is not None else config.theme
        if theme is False:
            self._clear_theme()
        elif theme:
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

    def _clear_theme(self):
        """Remove any GSTheme key from the sandbox defaults so the app loads the
        built-in base GSTheme (the control for a differential run). The seeded
        system GlobalDefaults may set GSTheme, so skipping _set_theme is not
        enough — the key must be deleted."""
        path = os.path.join(self.defaults_dir, "NSGlobalDomain.plist")
        try:
            with open(path, "rb") as f:
                data = plistlib.load(f)
        except Exception:
            return
        if data.pop("GSTheme", None) is not None:
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

    # ---- defaults seeding / inspection (for apps that persist their own domain) ----
    def domain_path(self, domain):
        """Path to a defaults domain plist inside the sandbox, e.g.
        'org.gershwin.Clock' -> <sandbox>/Defaults/org.gershwin.Clock.plist."""
        return os.path.join(self.defaults_dir, "%s.plist" % domain)

    def write_plist_file(self, domain, data):
        """Seed a defaults domain before launch. `data` may be a dict/list (written
        as an XML plist GNUstep reads) or raw str/bytes (written verbatim — for
        malformed-input tests)."""
        path = self.domain_path(domain)
        if isinstance(data, (bytes, bytearray)):
            with open(path, "wb") as f:
                f.write(data)
        elif isinstance(data, str):
            with open(path, "w") as f:
                f.write(data)
        else:
            with open(path, "wb") as f:
                plistlib.dump(data, f)

    def read_plist_file(self, domain):
        """Raw text of a defaults domain plist (e.g. after the app persisted it),
        or None. GNUstep may write OpenStep-format plists, so callers usually
        substring-match (assert a city/alarm name is present) rather than parse."""
        try:
            with open(self.domain_path(domain), "r", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def read_default(self, domain, key=None):
        """Read a persisted default via the GNUstep `defaults` tool under the
        sandbox env (reads the sandbox's domain, not the real one). Returns the
        printed value as text, or None. Use a `key` to scope the read — e.g. to
        tell one key's dict-valued entries from another's."""
        argv = [_defaults_tool(), "read", domain] + ([key] if key else [])
        try:
            p = subprocess.run(argv, env=self.env(), capture_output=True, text=True)
        except OSError:
            return None
        return p.stdout.strip() if p.returncode == 0 else None

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
