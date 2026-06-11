# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""Per-window screenshot capture + golden-image comparison.

Capture is per X11 window id (import -window <xid>) so a terminal or global menu
bar never pollutes a shot. Comparison uses ImageMagick `compare -metric AE`
(count of differing pixels). Goldens live in <GOLDENS>/<family>/<case>.png; diffs
on mismatch land in <ARTIFACTS>/. The golden/artifact roots come from
$GOLDSTEP_GOLDENS / $GOLDSTEP_ARTIFACTS, defaulting to ./goldens and ./artifacts
(relative to the current working directory). Set SPEC_UPDATE_GOLDENS=1 (or pass
update=True) to (re)write a baseline deliberately.
"""

import os
import subprocess
import time


def _root(env, name):
    v = os.environ.get(env)
    return v if v else os.path.join(os.getcwd(), name)


GOLDENS = _root("GOLDSTEP_GOLDENS", "goldens")
ARTIFACTS = _root("GOLDSTEP_ARTIFACTS", "artifacts")


def screen_frame_to_image(screen_frame, frame_box, screen_h, pad=0):
    """Map a screenFrame ({x,y,w,h}, GNUstep bottom-left screen coords) into
    image-space (x,y,w,h) top-left coords within a window captured at frame_box
    ({x,y,w,h} X11 top-left). pad expands the rect (e.g. for a pulse glow)."""
    sx, sy = screen_frame["x"], screen_frame["y"]
    sw, sh = screen_frame["w"], screen_frame["h"]
    img_x = sx - frame_box["x"] - pad
    img_y = (screen_h - (sy + sh)) - frame_box["y"] - pad
    return (img_x, img_y, sw + 2 * pad, sh + 2 * pad)


def sample_pixel(path, x, y):
    """(r,g,b) 0-255 of one pixel, via ImageMagick. For spot-checking a color
    (e.g. that the default button renders blue) without a full golden."""
    out = subprocess.run(
        ["convert", path, "-crop", "1x1+%d+%d" % (int(x), int(y)),
         "-depth", "8", "-format", "%[fx:int(r*255)],%[fx:int(g*255)],%[fx:int(b*255)]", "info:"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip()
    try:
        r, g, b = (int(v) for v in out.split(","))
        return (r, g, b)
    except ValueError:
        return None


def crop_ae(a, b, rect):
    """Differing-pixel count between two PNGs within an image-space (x,y,w,h)
    crop. For checking a localized change (e.g. a focus ring appearing)."""
    x, y, w, h = (int(v) for v in rect)
    crop = "%dx%d+%d+%d" % (w, h, x, y)
    ca, cb = "/tmp/_crop_a.png", "/tmp/_crop_b.png"
    subprocess.run(["convert", a, "-crop", crop, "+repage", ca], check=True)
    subprocess.run(["convert", b, "-crop", crop, "+repage", cb], check=True)
    r = subprocess.run(["compare", "-metric", "AE", ca, cb, "/tmp/_crop_diff.png"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace").strip().split()[0] if r.stdout else ""
    try:
        return int(float(out))
    except ValueError:
        return None


def capture(xid, path, settle=0.35, display=None):
    """Grab X11 window `xid` to PNG `path`. `display` (":N") targets a nested
    Xephyr; None => host $DISPLAY. Returns path."""
    time.sleep(settle)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    hexid = "0x%x" % int(xid)
    cmd = ["import"] + (["-display", display] if display else []) + ["-window", hexid, path]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("import failed for %s: %s" % (hexid, r.stderr.decode("utf-8", "replace")))
    return path


def capture_root(path, display, settle=0.35):
    """Grab the WHOLE nested framebuffer (the Xephyr root) to PNG `path`. Used for
    chrome that spans several top-level windows at once (a menu + its bar, a sheet
    over its parent). `display` is required (":N")."""
    time.sleep(settle)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = ["import", "-display", display, "-window", "root", path]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("root import failed: %s" % r.stderr.decode("utf-8", "replace"))
    return path


def _draw_rects(src, dst, rects, color="magenta"):
    """Copy src->dst with each (x,y,w,h) image-space rect filled flat. Used to
    blank out genuinely-animated widgets (default-button pulse, progress, spinner)
    so the rest of the window can be compared pixel-exactly."""
    args = ["convert", src, "-fill", color]
    for (x, y, w, h) in rects:
        args += ["-draw", "rectangle %d,%d %d,%d" % (int(x), int(y),
                                                     int(x + w), int(y + h))]
    args.append(dst)
    subprocess.run(args, check=True)


def _ae(a, b, diff_out):
    """Differing-pixel count between two PNGs via ImageMagick compare. Returns
    (count, same_dims). Writes a visual diff to diff_out."""
    os.makedirs(os.path.dirname(diff_out), exist_ok=True)
    r = subprocess.run(["compare", "-metric", "AE", a, b, diff_out],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode("utf-8", "replace").strip().split()[0] if r.stdout else ""
    # compare prints the metric to stderr/stdout; "compare: images differ in size"
    if "size" in r.stdout.decode("utf-8", "replace").lower():
        return (None, False)
    try:
        return (int(float(out)), True)
    except ValueError:
        return (None, True)


def compare_golden(family, case, shot_path, tolerance=0, update=None, mask_rects=None):
    """Compare shot against <GOLDENS>/<family>/<case>.png.

    mask_rects: image-space (x,y,w,h) rectangles blanked on BOTH images before
    comparing — for animated widgets. Returns (ok, message). On first run / update
    writes the baseline. On mismatch writes a diff PNG under <ARTIFACTS>/.
    """
    if update is None:
        update = bool(os.environ.get("SPEC_UPDATE_GOLDENS"))
    golden = os.path.join(GOLDENS, family, "%s.png" % case)

    if update or not os.path.exists(golden):
        os.makedirs(os.path.dirname(golden), exist_ok=True)
        subprocess.run(["cp", shot_path, golden], check=True)
        return (True, "wrote baseline %s/%s" % (family, case))

    a, b = golden, shot_path
    if mask_rects:
        os.makedirs(os.path.join(ARTIFACTS, family), exist_ok=True)
        a = os.path.join(ARTIFACTS, family, "%s.gold.masked.png" % case)
        b = os.path.join(ARTIFACTS, family, "%s.shot.masked.png" % case)
        _draw_rects(golden, a, mask_rects)
        _draw_rects(shot_path, b, mask_rects)

    diff_out = os.path.join(ARTIFACTS, family, "%s.diff.png" % case)
    count, same_dims = _ae(a, b, diff_out)
    if not same_dims:
        # keep the offending shot for inspection
        subprocess.run(["cp", shot_path, os.path.join(ARTIFACTS, family, "%s.got.png" % case)])
        return (False, "size mismatch vs golden (shot+diff in artifacts/%s)" % family)
    if count is None:
        return (False, "compare failed for %s/%s" % (family, case))
    if count <= tolerance:
        return (True, "%d px diff (<= %d)" % (count, tolerance))
    subprocess.run(["cp", shot_path, os.path.join(ARTIFACTS, family, "%s.got.png" % case)])
    return (False, "%d px differ (> %d); diff in artifacts/%s/%s.diff.png"
                   % (count, tolerance, family, case))
