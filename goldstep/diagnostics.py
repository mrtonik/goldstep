# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""Runtime-diagnostics launch wrappers for Session(launch_wrapper=...).

These build an argv prefix (or env tweak) that runs the app under a runtime
checker without the caller rebuilding anything:

    from goldstep import diagnostics as diag
    with Session(app, launch_wrapper=diag.valgrind("memcheck", log), \
                 ready_timeout=240) as s:
        ...

valgrind needs no instrumented build (it reaches into the system libraries too),
so it is the portable default. `asan_env`/`tsan_env` only do something if the app
+ theme were compiled with the matching `-fsanitize=` flag; they just tune the
runtime options. App-agnostic — nothing here knows about Eau.
"""

import shutil


def valgrind(tool="memcheck", log_path=None, *, extra=None, num_callers=40,
             suppressions=None, binary="valgrind"):
    """Build a valgrind launch-wrapper argv.

    tool: "memcheck" (use-after-free / invalid read-write / leaks),
          "helgrind" or "drd" (data races / lock-order — for threaded-DO bugs).
    log_path: where valgrind writes its report (kept out of the app's own log).
    """
    exe = shutil.which(binary) or binary
    argv = [exe, "--tool=%s" % tool, "--num-callers=%d" % num_callers,
            # don't let valgrind change the app's exit status; we read the log.
            "--error-exitcode=0", "--child-silent-after-fork=yes"]
    if tool == "memcheck":
        argv += ["--leak-check=full", "--track-origins=yes",
                 "--show-leak-kinds=definite,indirect"]
    elif tool in ("helgrind", "drd"):
        # GNUstep does its own locking; keep the report focused on real races.
        argv += ["--history-level=approx"] if tool == "helgrind" else []
    if log_path:
        argv.append("--log-file=%s" % log_path)
    for s in (suppressions or []):
        argv.append("--suppressions=%s" % s)
    argv += list(extra or [])
    return argv


def _san_env(env, var, options):
    env = dict(env or {})
    merged = dict(o.split("=", 1) for o in env.get(var, "").split(":") if "=" in o)
    merged.update(options)
    env[var] = ":".join("%s=%s" % kv for kv in merged.items())
    return env


def asan_env(env=None, **options):
    """Tune AddressSanitizer options (needs an `-fsanitize=address` build).
    Sensible defaults: abort on error with a full report, detect leaks on exit."""
    opts = {"abort_on_error": "1", "detect_leaks": "1",
            "halt_on_error": "1", "print_stats": "0"}
    opts.update({k: str(v) for k, v in options.items()})
    return _san_env(env, "ASAN_OPTIONS", opts)


def tsan_env(env=None, **options):
    """Tune ThreadSanitizer options (needs an `-fsanitize=thread` build)."""
    opts = {"halt_on_error": "0", "second_deadlock_stack": "1"}
    opts.update({k: str(v) for k, v in options.items()})
    return _san_env(env, "TSAN_OPTIONS", opts)
