<!-- SPDX-License-Identifier: BSD-2-Clause -->
# goldstep

A golden-image UI test harness for **GNUstep / Gershwin** apps. It drives a real
app through the **UIBridge** MCP server — widget tree, menus, XTEST mouse/keyboard
— under a throwaway defaults sandbox, optionally inside a private **Xephyr**, and
asserts on structure, the live tree, and per-window **golden-image** diffs.
Output is **TAP**.

It can also run the app under **valgrind / ASan / TSan** for runtime diagnostics
([§6](#6-runtime-diagnostics-valgrind--sanitizers-optional)), and do
**theme-vs-theme differential rendering** — any `GSTheme` against the built-in
base, as an independent oracle ([§5](#5-differential-rendering-theme-a-vs-theme-b)) —
neither of which needs the app rebuilt (valgrind) or changed.

No AI is involved: "MCP" here is just JSON-RPC over stdio, used as a plain
automation API. The harness is stdlib-only Python.

## Requirements

- A GNUstep / Gershwin desktop on **X11** (Linux or FreeBSD). Wayland is not
  supported — input uses XTEST.
- The app under test must be **attachable over UIBridge**, i.e. running under a
  theme that vends the UIBridge per-app service (the **Eau** theme does, by
  default on Gershwin).
- A **patched `UIBridgeServer`** (see [Notes](#notes)). Point `$UIBRIDGE_SERVER`
  at it, or have it on `$PATH`.
- Command-line tools: `xdotool`, ImageMagick (`import`, `convert`, `compare`),
  and — for `run_xephyr` — `Xephyr` and `xdpyinfo`.
- *(optional, for runtime diagnostics)* `valgrind`; or an `-fsanitize=address` /
  `-fsanitize=thread` build of the app + theme for `asan_env` / `tsan_env`.
- Python ≥ 3.8.

## Install

No build step; it's a single package.

```sh
git clone <your-repo> ~/build/goldstep
export PYTHONPATH=~/build/goldstep      # or: pip install -e ~/build/goldstep
```

## Quick start

```python
# smoke_test.py
from goldstep import run

def body(t, s):
    b = s.bridge
    t.ok(b.find(cls="NSButton"), "window has a button")
    t.eq(b.window_titles()[0], "MyApp", "title bar reads MyApp")

run("smoke", "/path/to/MyApp.app/MyApp", body)
```

```sh
UIBRIDGE_SERVER=~/Library/Tools/UIBridgeServer DISPLAY=:0 python3 smoke_test.py
```

Each test file is one TAP stream and exits non-zero if any check fails, so a
shell loop can aggregate pass/fail across files.

## Example

A complete worked example lives in the **Clock** app's repo
([mrtonik/Clock](https://github.com/mrtonik/Clock), under `tests-goldstep/`): a
12-file / 201-check suite covering tabs, alarms (incl. a real alarm firing),
timer, world clock, menus, i18n (EN/DE/FR/ES), persistence (restart + malformed
defaults), a modal quit guard, hostile-input fuzzing, and a golden-image layer —
all on goldstep. Its `clockkit.py` shows the recommended app-helper pattern.

## Testing your own GNUstep / Gershwin app

### 1. Point goldstep at your app

`run(name, launch, body)` (and `run_xephyr(...)`, below) take `launch` = your
built binary path (`Foo.app/Foo`), an argv list, or a callable returning one. The
harness sandboxes GNUstep user-defaults (your real prefs are untouched), launches
the app, attaches over UIBridge, waits until it is up, and hands `body` a session
`s` with `s.bridge`, `s.app`, and `s.sandbox`.

### 2. Drive it and assert — tree-only, no app changes needed

`s.bridge` is the whole API. It works purely off the live UIBridge tree, so an
uninstrumented app already gets structural + input + golden coverage:

```python
def body(t, s):
    b = s.bridge
    ok = next(w for w in b.find(cls="NSButton") if w["title"] == "OK")
    b.click_find_widget(ok)              # XTEST click
    b.type_text("hello")                 # XTEST typing (any Unicode)
    b.chord("s", ["control"])            # Ctrl+S
    t.ok(b.find(text="Saved"), "document saved")
```

Handy `Bridge` calls: `find(cls=, text=, tag=, visible_only=)`,
`window_titles()`, `list_menus()` / `menu_enabled(top, item)` /
`invoke_menu(top, item)`, `main_window_xid()`, `activate(xid)`, `mouse_move`,
`click`, `type_text`, `chord(key, modifiers)`, `click_find_widget`,
`click_dump_widget`.

### 3. Golden screenshots (optional)

```python
import goldstep.screenshot as S

def body(t, s):
    xid = s.bridge.main_window_xid()
    S.capture(xid, "artifacts/main.png", display=s.bridge.display)
    ok, msg = S.compare_golden("myapp", "main", "artifacts/main.png", tolerance=0)
    t.ok(ok, msg)
```

Goldens live under `$GOLDSTEP_GOLDENS` (default `./goldens/<family>/<case>.png`);
the first run writes the baseline, `SPEC_UPDATE_GOLDENS=1` rewrites it, diffs land
under `$GOLDSTEP_ARTIFACTS` (default `./artifacts`). For chrome that only renders
when the app is frontmost (panels, sheets, menus), use `run_xephyr(name, launch,
body)` — it runs the app as the only client on a private Xephyr display, and
`body` also receives the `xeph` (its `xeph.display` feeds
`S.capture(..., display=...)`).

### 4. Deeper state (optional instrumentation)

The UIBridge tree exposes structure and basic state. If you need cell state,
first-responder, default-ness, or exact fonts, instrument your app to write a JSON
snapshot of its widgets on a timer, and read it via a `StateDump`:

- **App side:** every `$GOLDSTEP_DUMP_INTERVAL` seconds, write
  `$GOLDSTEP_DUMP_DIR/<appname>.state.json`, shaped like
  `{"widgets": [{"identifier": ..., "tag": ..., "stringValue": ...,
  "screenFrame": {"x":..,"y":..,"w":..,"h":..}, ...}]}`. (Both env vars are set
  for you in the launched app's environment.)
- **Test side:**

  ```python
  from goldstep import run, StateDump

  run("t", "/path/MyApp.app/MyApp", body,
      app_name="MyApp",
      state_dump=lambda sb: StateDump(sb, "MyApp"),
      require_dump=True)               # wait for the first dump before ready

  # inside body:
  d = s.bridge.dump()
  w = s.bridge.widget(d, identifier="emailField")
  ```

Without this, `s.bridge.dump()` raises and you stay tree-only.

### 5. Differential rendering (theme A vs theme B)

`Session`/`Sandbox` take a `theme=` argument: an absolute bundle path forces that
`GSTheme`, `None` inherits the configured/system theme, and **`theme=False` forces
the built-in base theme** by removing any inherited `GSTheme` key — the control for
a differential run. Render the same app under two themes and diff per widget, using
base as an independent oracle for "these states should look different":

```python
from goldstep import Session
import goldstep.screenshot as S

def capture(label, theme):
    with Session(app, display=disp, theme=theme) as s:
        xid = s.bridge.main_window_xid()
        S.capture(xid, "artifacts/%s.png" % label, display=s.bridge.display)

capture("themed", "/path/Dev.theme")
capture("base", False)           # built-in base GSTheme
```

### 6. Runtime diagnostics (valgrind / sanitizers, optional)

`Session(launch_wrapper=...)` prefixes the app with a launcher so it runs under a
runtime checker — no rebuild for valgrind. `goldstep.diagnostics` builds the
wrappers:

```python
from goldstep import Session
from goldstep import diagnostics as diag

wrap = diag.valgrind("memcheck", "artifacts/mc.log")   # or "helgrind" / "drd"
with Session(app, display=disp, launch_wrapper=wrap,
             require_dump=True, ready_timeout=900) as s:   # see caveats
    s.bridge.find(cls="NSButton")
```

`asan_env(env, **opts)` / `tsan_env(...)` tune sanitizer options for an app already
built with the matching `-fsanitize=` flag. Caveats: GNUstep startup under valgrind
is minutes-slow — use `require_dump=True` + a large `ready_timeout` so ready-detection
waits for the *real* app, not a fallback tree; and `memcheck` serialises threads, so
use `helgrind`/`drd` (or a TSan build) to find data races.

### Environment overrides

| Variable | Purpose |
|---|---|
| `UIBRIDGE_SERVER` | Path to the patched server (else `$PATH`, else `~/Library/Tools/UIBridgeServer`). |
| `GOLDSTEP_THEME` | Absolute path to a GSTheme bundle to force (e.g. a dev-theme build); default uses the system theme. |
| `GOLDSTEP_GOLDENS` / `GOLDSTEP_ARTIFACTS` | Golden / diff roots (default `./goldens`, `./artifacts`). |
| `GNUSTEP_CONFIG_FILE` / `GOLDSTEP_GLOBAL_DEFAULTS` | Override GNUstep config / GlobalDefaults discovery if not in the usual locations. |
| `GOLDSTEP_KEEP` | Keep the throwaway sandbox dir for inspection. |

## Notes

- **X11 only.** Input is XTEST; GNUstep ignores synthetic `XSendEvent`. There is
  no Wayland path.
- **A patched `UIBridgeServer` is required.** Stock UIBridge sends input via
  `XSendEvent`, which GNUstep drops; goldstep needs the XTEST input patches and
  the `x11_chord` / `x11_activate_window` tools.
- **`invoke_selector` is never used** — it use-after-frees the bridge on some
  builds. State comes from the tree (or a `StateDump`); input from XTEST.

## License

BSD-2-Clause. See [LICENSE](LICENSE).
