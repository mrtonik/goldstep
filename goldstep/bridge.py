# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""Bridge: a thin, app-agnostic facade over the stable UIBridge tools.

Groups the UIBridge MCP tools a UI test needs — widget tree (attach/get_root/
find_widgets), menus (list_menus + helpers), X11 window geometry, and XTEST
input (mouse/keyboard) — plus the GNUstep->X11 coordinate mapping used to drive
and screenshot widgets.

It works purely off the live UIBridge tree, so uninstrumented apps still get
structural + golden coverage. An optional state-dump (set on .state_dump, e.g.
for apps that expose cell state / first-responder that the tree omits) is the
only app-side instrumentation, and is never required.

NOTE: invoke_selector is deliberately never called here — on some bridge builds
it use-after-frees the target. State comes from the tree (or a dump); geometry
from find_widgets; input from XTEST.
"""

import os
import re
import subprocess
import time

from .mcp import MCPError


class Bridge:
    def __init__(self, client, display=None):
        self.c = client
        self.pid = None
        self.state_dump = None          # optional; see statedump.StateDump
        self._screen_h = None
        # X display the app renders into. None => host $DISPLAY; ":N" => a private
        # Xephyr. Direct xwininfo/xdotool calls are pinned to it; the MCP x11_*
        # tools are pinned via the server's own DISPLAY (set by Session).
        self.display = display or os.environ.get("DISPLAY")
        # In a nested Xephyr the app is the only client, but _NET_WM_PID reads
        # back as 0 there, so pid-filtering finds nothing — drop it and key off
        # window geometry instead (safe: nothing else is on that display).
        self.nested = display is not None

    # ---- attach / tree ----
    def attach(self, pid):
        return self.c.call_json("attach_app", {"pid": pid})

    def get_root(self):
        return self.c.call_json("get_root")

    def get_details(self, oid):
        return self.c.call_json("get_object_details", {"object_id": oid})

    def find(self, cls=None, text=None, tag=None, visible_only=True):
        args = {}
        if cls is not None: args["class"] = cls
        if text is not None: args["text"] = text
        if tag is not None: args["tag"] = tag
        if visible_only is not None: args["visible_only"] = visible_only
        r = self.c.call_json("find_widgets", args)
        return r if isinstance(r, list) else []

    def by_tag(self, tag):
        hits = self.find(tag=tag, visible_only=False)
        return hits[0] if hits else None

    def windows(self):
        return self.get_root().get("windows", [])

    def window_titles(self, visible_only=True):
        return [w.get("title") for w in self.windows()
                if not (visible_only and w.get("hidden"))]

    # ---- optional state dump (ground truth for instrumented apps) ----
    def dump(self, after=None, timeout=4):
        """Read the app's latest JSON state-dump. Requires .state_dump to be set
        (opt-in instrumentation); raises otherwise. `after` (epoch) waits for a
        dump written at/after that time."""
        if self.state_dump is None:
            raise MCPError("no state-dump configured (app not instrumented); "
                           "use the live tree (find_widgets) instead")
        return self.state_dump.read(after=after, timeout=timeout)

    @staticmethod
    def widget(dump, identifier=None, tag=None):
        """Find a widget in a state-dump by identifier or tag."""
        for w in dump.get("widgets", []):
            if identifier is not None and w.get("identifier") == identifier:
                return w
            if tag is not None and w.get("tag") == tag:
                return w
        return None

    # ---- menus (via the bridge; no pixels) ----
    def list_menus(self):
        return self.c.call_json("list_menus")

    def menu_tree(self):
        """Flatten list_menus into {top_title: {enabled, items:[{title,enabled,
        state,keyEquivalent,isSeparator,indexPath}]}}. The bar may be returned
        twice (windowed + app copy); keep the first of each top title."""
        raw = self.list_menus()
        out = {}
        for entry in (raw if isinstance(raw, list) else [raw]):
            for top in entry.get("menu", {}).get("items", []):
                title = top.get("title")
                if title in out:
                    continue
                sub = top.get("submenu", {}) or {}
                out[title] = {
                    "enabled": top.get("enabled"),
                    "items": [{"title": it.get("title"), "enabled": it.get("enabled"),
                               "state": it.get("state"),
                               "keyEquivalent": it.get("keyEquivalent"),
                               "isSeparator": it.get("isSeparator"),
                               "indexPath": it.get("indexPath")}
                              for it in sub.get("items", [])]}
        return out

    def top_titles(self):
        return list(self.menu_tree().keys())

    def menu_item(self, top, item):
        for it in self.menu_tree().get(top, {}).get("items", []):
            if it.get("title") == item:
                return it
        return None

    def menu_enabled(self, top, item):
        it = self.menu_item(top, item)
        return bool(it and it.get("enabled"))

    def menu_item_id(self, top_title, item_title):
        """Build the 'menuitem:<windowId>:<top>.<item>' id invokeMenuItem expects,
        from list_menus (windowId + indexPaths)."""
        raw = self.list_menus()
        for entry in (raw if isinstance(raw, list) else [raw]):
            wid = entry.get("windowId")
            if wid is None:
                continue
            for top in entry.get("menu", {}).get("items", []):
                if top.get("title") != top_title:
                    continue
                ti = top.get("indexPath", [0])[0]
                for it in (top.get("submenu", {}) or {}).get("items", []):
                    if it.get("title") == item_title:
                        return "menuitem:%s:%s.%s" % (wid, ti, it.get("indexPath", [0])[0])
        return None

    def invoke_menu(self, top, item):
        """Trigger a menu item by titles. Do NOT use for items that open a modal
        (it blocks until the action returns)."""
        mid = self.menu_item_id(top, item)
        if not mid:
            raise MCPError("menu item not found: %s > %s" % (top, item))
        return self.c.call_json("invoke_menu_item", {"object_id": mid})

    # ---- X11 ----
    def x11_windows(self):
        return self.c.call_json("x11_list_windows").get("windows", [])

    def main_window_box(self, min_w=120, min_h=60):
        """The X11 frame window to screenshot: our pid's largest real window.
        Returns {'id','x','y','w','h'} (X11 top-left coords) or None."""
        best = None
        for w in self.x11_windows():
            if not self.nested and w.get("pid") != self.pid:
                continue
            if w.get("width", 0) < min_w or w.get("height", 0) < min_h:
                continue
            area = w["width"] * w["height"]
            if best is None or area > best[1]:
                best = ({"id": w["id"], "x": w["x"], "y": w["y"],
                         "w": w["width"], "h": w["height"]}, area)
        return best[0] if best else None

    def main_window_xid(self, **kw):
        box = self.main_window_box(**kw)
        return box["id"] if box else None

    def screen_size(self):
        """(width, height) of the X11 root, cached — to flip GNUstep Y coords."""
        if self._screen_h is not None:
            return self._screen_h
        out = subprocess.run(["xwininfo", "-display", self.display, "-root"],
                             capture_output=True, text=True).stdout
        w = int(re.search(r"Width:\s+(\d+)", out).group(1))
        h = int(re.search(r"Height:\s+(\d+)", out).group(1))
        self._screen_h = (w, h)
        return self._screen_h

    def window_xid_by_size(self, w, h, tol=2):
        box = self.window_box_by_size(w, h, tol)
        return box["id"] if box else None

    def window_box_by_size(self, w, h, tol=2):
        """Full {id,x,y,w,h} of a window matched by size — for chrome windows
        (panel/sheet/menu) we know the dimensions of but not the id."""
        for xw in self.x11_windows():
            if not self.nested and xw.get("pid") != self.pid:
                continue
            if abs(xw.get("width", -1) - w) <= tol and abs(xw.get("height", -1) - h) <= tol:
                return {"id": xw["id"], "x": xw["x"], "y": xw["y"],
                        "w": xw["width"], "h": xw["height"]}
        return None

    def activate(self, xid):
        self.c.call_json("x11_activate_window", {"xid": int(xid)})
        time.sleep(0.2)

    def mouse_move(self, x, y):
        return self.c.call_json("x11_mouse_move", {"x": int(x), "y": int(y)})

    def click(self, button=1):
        return self.c.call_json("x11_click", {"button": button})

    def type_text(self, text):
        return self.c.call_json("x11_type", {"text": text})

    def chord(self, key, modifiers=None):
        """Tap `key` (a char, or an X keysym name like 'Return'/'Left'/'F5') while
        holding `modifiers` (names: control/ctrl, alt/meta, shift, super/win)."""
        args = {"key": key}
        if modifiers:
            args["modifiers"] = modifiers
        return self.c.call_json("x11_chord", args)

    def _screen_xy(self, widget):
        """Center of a widget (with a GNUstep screenFrame) in absolute X11 coords
        (Y flipped)."""
        sf = widget.get("screenFrame")
        if not sf:
            return None
        _, h = self.screen_size()
        return (int(sf["x"] + sf["w"] / 2.0), int(h - (sf["y"] + sf["h"] / 2.0)))

    def hover(self, widget):
        """Move the pointer onto a widget (no click) to trigger any hover state."""
        xy = self._screen_xy(widget)
        if xy:
            self.mouse_move(*xy)
        return xy

    def park_mouse(self):
        self.mouse_move(3, 3)

    def mouse_down(self, widget):
        """Press-and-HOLD the primary button on a widget (xdotool; the MCP click
        does press+release together). Pair with mouse_up()."""
        xy = self._screen_xy(widget)
        if not xy:
            return False
        subprocess.run(["xdotool", "mousemove", str(xy[0]), str(xy[1]), "mousedown", "1"],
                       check=False, env=self._xenv())
        return True

    def mouse_up(self):
        subprocess.run(["xdotool", "mouseup", "1"], check=False, env=self._xenv())

    def _xenv(self):
        """Env for direct xdotool calls: this xdotool honours $DISPLAY, not a
        flag, so pin it to our (possibly nested) display."""
        e = dict(os.environ); e["DISPLAY"] = self.display
        return e

    def click_find_widget(self, w):
        """Click a widget returned by find_widgets (its screen_frame is a string).
        Works for alert buttons during a modal (a state-dump is stopped then)."""
        nums = re.findall(r"[-\d.]+", w.get("screen_frame") or "")
        if len(nums) < 4:
            return False
        x, y, wd, h = (float(n) for n in nums[:4])
        return self.click_dump_widget({"screenFrame": {"x": x, "y": y, "w": wd, "h": h}})

    def alert_button(self, title):
        for w in self.find(cls="NSButton", visible_only=True):
            if w.get("title") == title:
                return w
        return None

    def click_dump_widget(self, widget, dx=None, dy=None):
        """Click the centre (or an offset) of a widget with a GNUstep screenFrame,
        mapping it to absolute X11 coords. Used to drive interactive states
        (focus, press) via XTEST."""
        sf = widget.get("screenFrame")
        if not sf:
            return False
        _, h = self.screen_size()
        x = sf["x"] + (dx if dx is not None else sf["w"] / 2.0)
        ytop = h - (sf["y"] + sf["h"])  # X11 top edge
        y = ytop + (dy if dy is not None else sf["h"] / 2.0)
        self.mouse_move(int(x), int(y))
        self.click()
        return True
