#!/usr/bin/env python3
"""kishot: presentation sheets of a KiCad board, ready to send.

Two sheets, each one PNG under a header (title, revision, date, company,
size, copper layers, footprints):

  3d      six 3D views: top, bottom, three three-quarter views, profile
  layers  the tracks of every copper layer, copper pours hidden

kishot prints the path of each image it writes on stdout, one per line, and
nothing else; progress and errors go to stderr. It needs kicad-cli (KiCad 9 or
later) and ImageMagick 7 (`magick`).
"""

import argparse
import datetime
import difflib
import io
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

__version__ = "0.4.0"

SHEETS = ("3d", "layers", "net", "diff", "sch-diff", "3d-diff", "fade", "spin")
NEEDS_DIFF = ("diff", "sch-diff", "3d-diff", "fade")

# (file, caption, kicad-cli options)
VIEWS = [
    ("1", "Top", ["--side", "top"]),
    ("2", "Bottom", ["--side", "bottom"]),
    ("3", "Front three-quarter", ["--side", "top", "--rotate", "-50,0,30", "--perspective", "--zoom", "0.8"]),
    ("4", "Rear three-quarter", ["--side", "top", "--rotate", "-50,0,210", "--perspective", "--zoom", "0.8"]),
    ("5", "Underside three-quarter", ["--side", "bottom", "--rotate", "-50,0,-30", "--perspective", "--zoom", "0.8"]),
    ("6", "Profile", ["--side", "front"]),
]

PAPER = "#f3f4f6"
INK = "#111827"
GREY = "#6b7280"

# A pour whose outline covers this share of the board is called a plane.
PLANE_SHARE = 0.3


class Failure(Exception):
    """An error worth one line on stderr, without a traceback."""


# --- reading the board ---------------------------------------------------------

TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]')


def closing_paren(s, i):
    """Index of the parenthesis that closes the one at s[i], quoted strings skipped."""
    depth = 0
    for m in TOKEN.finditer(s, i):
        t = m.group()
        if t == "(":
            depth += 1
        elif t == ")":
            depth -= 1
            if depth == 0:
                return m.start()
    raise Failure("unbalanced parentheses in the board file")


def top_level_spans(s):
    """The direct children of the root expression, as (head, start, end) triples."""
    spans, depth, start = [], 0, None
    for m in TOKEN.finditer(s):
        t = m.group()
        if t == "(":
            depth += 1
            if depth == 2:
                start = m.start()
        elif t == ")":
            if depth == 2:
                head = re.match(r"\(\s*([^\s()]+)", s[start:start + 64])
                spans.append((head.group(1) if head else "", start, m.end()))
            depth -= 1
    return spans


def top_level_items(s):
    """The direct children of the root expression, as (head, text) pairs."""
    return [(head, s[a:b]) for head, a, b in top_level_spans(s)]


def strip_fills(s):
    """The board without its filled zone areas; the zone outlines stay."""
    out, pos = [], 0
    for m in re.finditer(r"\(filled_polygon\b", s):
        if m.start() < pos:
            continue
        start, end = m.start(), closing_paren(s, m.start()) + 1
        line = s.rfind("\n", pos, start)
        if line >= 0 and not s[line + 1:start].strip():
            start = line
        out.append(s[pos:start])
        pos = end
    out.append(s[pos:])
    return "".join(out)


def title_block(text):
    i = text.find("(title_block")
    block = text[i:closing_paren(text, i) + 1] if i >= 0 else ""

    def field(k):
        m = re.search(rf'\({k} "((?:[^"\\]|\\.)*)"\)', block)
        return m.group(1) if m else ""

    return {k: field(k) for k in ("title", "rev", "date", "company")}


def points(text):
    return [(float(x), float(y)) for x, y in
            re.findall(r"\((?:start|end|mid|center|xy) (-?[\d.]+) (-?[\d.]+)\)", text)]


def outline_box(items):
    """Bounding box of the board outline (Edge.Cuts), or None."""
    pts = []
    for head, text in items:
        if not head.startswith("gr_") or '(layer "Edge.Cuts")' not in text:
            continue
        p = points(text)
        if head == "gr_circle" and len(p) >= 2:
            (cx, cy), (ex, ey) = p[0], p[1]
            r = math.hypot(ex - cx, ey - cy)
            p = [(cx - r, cy - r), (cx + r, cy + r)]
        pts += p
    if not pts:
        return None
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


def copper_layers(items):
    """Copper layers in stack order: (name, user name)."""
    block = next((t for h, t in items if h == "layers"), "")
    layers = [(n, alias) for n, alias in
              re.findall(r'\(\d+ "([^"]+\.Cu)" \w+(?: "([^"]*)")?\)', block)]

    def rank(layer):
        n = layer[0]
        if n == "F.Cu":
            return 0
        if n == "B.Cu":
            return 10_000
        m = re.match(r"In(\d+)\.Cu", n)
        return int(m.group(1)) if m else 5_000

    return sorted(layers, key=rank)


def zone_layers(text, all_copper):
    m = re.search(r"\(layers? ((?:\"[^\"]+\"\s*)+)\)", text)
    names = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    out = set()
    for n in names:
        if n == "*.Cu":
            out.update(all_copper)
        elif n == "F&B.Cu":
            out.update({"F.Cu", "B.Cu"})
        else:
            out.add(n)
    return out


def planes(items, layers, box):
    """Per copper layer, the nets of the pours large enough to be called planes."""
    found = {n: [] for n, _ in layers}
    if not box:
        return found
    board = (box[2] - box[0]) * (box[3] - box[1])
    for head, text in items:
        if head != "zone" or "(keepout" in text:
            continue
        net = re.search(r'\(net_name "([^"]*)"\)', text) or re.search(r'\(net "([^"]*)"\)', text)
        if not net or not net.group(1):
            continue
        i = text.find("(polygon")
        if i < 0:
            continue
        p = points(text[i:closing_paren(text, i)])
        if not p:
            continue
        xs, ys = zip(*p)
        if (max(xs) - min(xs)) * (max(ys) - min(ys)) < PLANE_SHARE * board:
            continue
        for layer in zone_layers(text, found):
            if layer in found and net.group(1) not in found[layer]:
                found[layer].append(net.group(1))
    return found


def read_board(pcb, text=None):
    """What the sheets need from a board: `text` is its content when it does not come
    from the file itself (an older revision, for --diff)."""
    if text is None:
        text = pcb.read_text(encoding="utf-8")
    items = top_level_items(text)
    tb = title_block(text)
    sch = pcb.with_suffix(".kicad_sch")
    if not tb["title"] and sch.exists():
        # The board's title block is often the template's, left empty.
        tb_sch = title_block(sch.read_text(encoding="utf-8"))
        if tb_sch["title"]:
            tb = tb_sch
    box = outline_box(items)
    layers = copper_layers(items)
    netmap = net_table(items)
    tracks = {n: 0 for n, _ in layers}
    length = {n: 0.0 for n, _ in layers}
    widths, vias = [], 0
    for head, t in items:
        if head in ("segment", "arc"):
            m = re.search(r'\(layer "([^"]+)"\)', t)
            if m and m.group(1) in tracks:
                tracks[m.group(1)] += 1
                length[m.group(1)] += track_length(head, t)
            w = re.search(r"\(width ([\d.]+)\)", t)
            if w:
                widths.append(float(w.group(1)))
        elif head == "via":
            vias += 1
    return {
        "text": text,
        "title_block": tb,
        "box": box,
        "layers": layers,
        "tracks": tracks,
        "planes": planes(items, layers, box),
        "footprints": sum(1 for h, _ in items if h == "footprint"),
        "items": items,
        "netmap": netmap,
        "length": length,
        "vias": vias,
        "min_width": min(widths) if widths else None,
        "net_classes": net_classes(pcb.with_suffix(".kicad_pro")),
    }


def net_table(items):
    """Net numbers to names, from the board's net table (KiCad 9 and older); empty when the
    items carry their net by name (KiCad 10)."""
    table = {}
    for head, t in items:
        if head == "net":
            m = re.match(r'\(net (\d+) "((?:[^"\\]|\\.)*)"\)', t)
            if m:
                table[m.group(1)] = m.group(2)
    return table


def item_net(text, netmap):
    """The net of a track, via, pad or zone: by name, or by number through the table."""
    m = re.search(r'\(net (?:(\d+)(?: "((?:[^"\\]|\\.)*)")?|"((?:[^"\\]|\\.)*)")\)', text)
    if not m:
        return ""
    if m.group(3) is not None:
        return m.group(3)
    if m.group(2) is not None:
        return m.group(2)
    return netmap.get(m.group(1), "")


def track_length(head, text):
    """Length of a segment, or of an arc through its start, middle and end, in mm."""
    p = dict((k, (float(x), float(y))) for k, x, y in
             re.findall(r"\((start|mid|end) (-?[\d.]+) (-?[\d.]+)\)", text))
    if "start" not in p or "end" not in p:
        return 0.0
    (ax, ay), (cx, cy) = p["start"], p["end"]
    if head != "arc" or "mid" not in p:
        return math.hypot(cx - ax, cy - ay)
    bx, by = p["mid"]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return math.hypot(cx - ax, cy - ay)
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    t1, t2, t3 = (math.atan2(y - uy, x - ux) for x, y in ((ax, ay), (bx, by), (cx, cy)))
    sweep = (t3 - t1) % math.tau
    if (t2 - t1) % math.tau > sweep:  # the arc goes the other way round
        sweep = math.tau - sweep
    return r * sweep


def net_classes(pro):
    try:
        data = json.loads(pro.read_text(encoding="utf-8"))
        return [c.get("name", "") for c in data.get("net_settings", {}).get("classes", [])]
    except (OSError, ValueError, AttributeError):
        return []


def stats_details(board):
    """The copper in figures, for the header of the copper sheets."""
    total = sum(board["length"].values())
    out = [f"{total / 1000:.2f} m of track" if total >= 1000 else f"{total:.0f} mm of track",
           f"{board['vias']} via{'s' * (board['vias'] != 1)}"]
    if board["min_width"]:
        out.append(f"narrowest track {board['min_width']:g} mm")
    classes = [c for c in board["net_classes"] if c]
    if len(classes) > 1:
        out.append("net classes " + ", ".join(classes))
    return out


def mm_text(length):
    return f"{length / 1000:.2f} m" if length >= 1000 else f"{length:.0f} mm"


def track_keys(items):
    """Every track and via, keyed by its geometry: what --diff compares. Ids and net
    numbers are left out, so a track that did not move is the same in both versions."""
    tracks, vias = {}, set()
    for head, t in items:
        geo = tuple(re.findall(r"\((start|mid|end|width|at|size|drill) ([^()]*)\)", t))
        if head in ("segment", "arc"):
            m = re.search(r'\(layer "([^"]+)"\)', t)
            if m:
                tracks.setdefault(m.group(1), set()).add((head, geo))
        elif head == "via":
            vias.add(geo + tuple(re.findall(r'"([^"]+\.Cu)"', t)))
    return tracks, vias


# --- drawing -------------------------------------------------------------------

def run(cmd, what):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise Failure(f"{what} failed ({cmd[0]} exit {r.returncode}):\n{(r.stderr or r.stdout)[-800:]}")
    return r.stdout


def font(pattern, fallback):
    try:
        f = subprocess.run(["fc-match", "-f", "%{file}", pattern],
                           capture_output=True, text=True, check=True).stdout
        if f.endswith((".ttf", ".otf")):
            return f
    except (OSError, subprocess.CalledProcessError):
        pass
    return fallback


class Style:
    def __init__(self, scale):
        self.k = scale
        self.regular = font("DejaVu Sans", "DejaVu-Sans")
        self.bold = font("DejaVu Sans:bold", "DejaVu-Sans-Bold")

    def px(self, n):
        return max(1, int(n * self.k))


def tile(src, dst, caption, size, style, trim):
    """One picture centred on a grey tile, its caption underneath."""
    w, h = size
    pre = ["-channel", "A", "-threshold", "60%", "+channel", "-trim", "+repage"] if trim else ["-trim", "+repage"]
    run(["magick", str(src), *pre,
         "-resize", f"{int(w * .92)}x{int(h * .86)}",
         "-background", PAPER, "-gravity", "center", "-extent", f"{w}x{int(h * .9)}",
         "(", "-size", f"{w}x{int(h * .1)}", f"xc:{PAPER}",
         "-font", style.regular, "-pointsize", str(style.px(30)), "-fill", GREY,
         "-gravity", "center", "-annotate", "+0+0", caption, ")",
         "-append", "+repage", str(dst)], f"tiling {caption}")
    return dst


def sheet(tiles, columns, header, out, style, work):
    """Tiles in a grid, a header above, a footer below."""
    grid = work / f"grid-{out.stem}.png"
    g = style.px(14)
    run(["magick", "montage", *map(str, tiles), "-tile", f"{columns}x", "-geometry", f"+{g}+{g}",
         "-background", "white", str(grid)], "montage")
    width = int(run(["magick", "identify", "-format", "%w", str(grid)], "identify"))
    title, details, footer = header
    m = style.px(40)
    run(["magick",
         "(", "-size", f"{width}x{style.px(190)}", "xc:white", "-gravity", "northwest",
         "-font", style.bold, "-pointsize", str(style.px(58)), "-fill", INK,
         "-annotate", f"+{m}+{style.px(36)}", title,
         "-font", style.regular, "-pointsize", str(style.px(30)), "-fill", GREY,
         "-annotate", f"+{m}+{style.px(122)}", "   ·   ".join(details), ")",
         str(grid),
         "(", "-size", f"{width}x{style.px(60)}", "xc:white",
         "-font", style.regular, "-pointsize", str(style.px(22)), "-fill", GREY,
         "-gravity", "east", "-annotate", f"+{m}+0", footer, ")",
         "-append", "+repage", "-depth", "8", "-strip", str(out)], "assembling the sheet")
    return out


def board_details(board):
    tb = board["title_block"]
    box = board["box"]
    n = len(board["layers"])
    return [x for x in (
        f"rev. {tb['rev']}" if tb["rev"] else "",
        tb["date"], tb["company"],
        f"{box[2] - box[0]:.1f} × {box[3] - box[1]:.1f} mm" if box else "",
        f"{n} copper layer{'s' * (n != 1)}" if n else "",
        f"{board['footprints']} footprints") if x]


def header(pcb, board, subtitle, details=None):
    """Title, details and footer of a sheet; the note and the DRC summary are added here."""
    tb = board["title_block"]
    details = board_details(board) if details is None else list(details)
    if board.get("note"):
        details.insert(0, board["note"])
    if board.get("drc"):
        details.append(board["drc"]["summary"])
    kicad = run(["kicad-cli", "--version"], "kicad-cli --version").strip()
    footer = f"{pcb.name}  ·  {subtitle}  ·  KiCad {kicad}, {datetime.date.today():%Y-%m-%d}"
    return tb["title"] or pcb.stem, details, footer


def touches_edge(png):
    """True when the opaque part of a render reaches a side of the image."""
    out = run(["magick", str(png), "-alpha", "extract", "-threshold", "60%",
               "-format", "%w %h %@", "info:"], "measuring a render")
    m = re.match(r"(\d+) (\d+) (\d+)x(\d+)\+(\d+)\+(\d+)", out.strip())
    if not m:
        return False
    w, h, bw, bh, x, y = map(int, m.groups())
    return x == 0 or y == 0 or x + bw >= w or y + bh >= h


def render_view(pcb, view, size, quality, png):
    """One `kicad-cli pcb render` view, zoomed out when something leaves the frame."""
    name, caption, opts = view
    zoom = float(opts[opts.index("--zoom") + 1]) if "--zoom" in opts else 1.0
    base = [o for i, o in enumerate(opts) if o != "--zoom" and (i == 0 or opts[i - 1] != "--zoom")]
    # A part that sticks out (a cable, a long connector) can leave the
    # frame: zoom out and render again, at most twice.
    for attempt in range(3):
        run(["kicad-cli", "pcb", "render", "-w", str(size[0]), "-h", str(size[1]),
             "--quality", quality, "--background", "transparent",
             # The default preset follows the plot settings, which hide the
             # solder mask: the board would come out bare copper.
             "--preset", "follow_pcb_editor", *base, "--zoom", f"{zoom:g}",
             "-o", str(png), str(pcb)],
            f"rendering view {name}")
        if not png.exists():
            raise Failure(f"kicad-cli wrote no image for view {name}")
        if not touches_edge(png):
            break
        zoom *= 0.7
    else:
        say(f"view {caption.lower()} still touches the frame at zoom {zoom / 0.7:.2f}")
    return png


def make_3d(pcb, board, out, fast, work):
    size = (900, 650) if fast else (1800, 1300)
    cell = (600, 430) if fast else (1100, 800)
    quality = "basic" if fast else "high"
    style = Style(cell[0] / 1100)

    def render(view):
        png = render_view(pcb, view, size, quality, work / f"3d-{view[0]}.png")
        return tile(png, work / f"3d-tile-{view[0]}.png", view[1], cell, style, trim=True)

    with ThreadPoolExecutor(max_workers=min(len(VIEWS), os.cpu_count() or 2)) as ex:
        tiles = list(ex.map(render, VIEWS))
    return sheet(tiles, 3, header(pcb, board, "3D views"), out, style, work)


SPIN_TILT = -55  # degrees: the board seen from above, at a slant
SPIN_SECONDS = 4.8  # one turn


def opaque_box(png):
    """x0, y0, x1, y1 of the opaque part of a render."""
    out = run(["magick", str(png), "-alpha", "extract", "-threshold", "60%", "-format", "%@", "info:"],
              "measuring a render")
    m = re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)", out.strip())
    if not m:
        return None
    w, h, x, y = map(int, m.groups())
    return x, y, x + w, y + h


# KiCad's 3D viewer background, for the PS2 version.
VIEWER_TOP, VIEWER_BOTTOM = "#ccccE6", "#666680"


def opaque_box_of(mask):
    """x0, y0, x1, y1 of the white part of a black-and-white mask, or None."""
    out = run(["magick", str(mask), "-colorspace", "gray", "-threshold", "50%", "-format", "%@", "info:"],
              "measuring")
    m = re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)", out.strip())
    if not m or int(m.group(1)) <= 1 and int(m.group(2)) <= 1:
        return None
    w, h, x, y = map(int, m.groups())
    return x, y, x + w, y + h


def gif(frames, out, delay, transparent=False):
    """Frames to an animated GIF with one palette for all of them (`+remap`): per-frame
    palettes make the colours flicker as the board turns."""
    run(["magick", "-delay", str(delay), "-dispose", "Background" if transparent else "None",
         *map(str, frames), "-dither", "None", "+remap", "-loop", "0",
         *([] if transparent else ["-layers", "Optimize"]), str(out)], f"assembling {out.name}")
    return out


def make_spin(pcb, board, out, fast, work):
    """Three animated GIFs from one turn rendered by KiCad: on a light background with the
    board's name, transparent without text, and pixelated in the manner of a PS2 game."""
    n = 72 if fast else 96  # 14 and 20 frames a second
    size = (480, 360) if fast else (800, 600)
    render = (int(size[0] * 1.25), int(size[1] * 1.25))  # cropped down afterwards
    quality = "basic" if fast else "high"
    zoom = 0.75

    def frame(i):
        png = work / f"spin-{i:03d}.png"
        run(["kicad-cli", "pcb", "render", "-w", str(render[0]), "-h", str(render[1]),
             "--quality", quality, "--background", "transparent", "--preset", "follow_pcb_editor",
             "--rotate", f"{SPIN_TILT},0,{360 * i / n:.2f}", "--perspective", "--zoom", f"{zoom:g}",
             "-o", str(png), str(pcb)], f"rendering frame {i + 1} of {n}")
        if not png.exists():
            raise Failure(f"kicad-cli wrote no image for frame {i + 1}")
        return png

    # Every frame is checked: a part that sticks out can leave the frame at one angle only.
    for attempt in range(3):
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
            frames = list(ex.map(frame, range(n)))
        boxes = [opaque_box(f) for f in frames]
        cut = any(b and (b[0] == 0 or b[1] == 0 or b[2] >= render[0] or b[3] >= render[1]) for b in boxes)
        if not cut:
            break
        zoom *= 0.7
    else:
        say(f"the board still touches the frame at zoom {zoom / 0.7:.2f}")
    boxes = [b for b in boxes if b]
    if not boxes:
        raise Failure("kicad-cli rendered nothing")
    # One crop for every frame, the union of what they show: the board turns in place
    # instead of being re-centred, and jumping, at each frame.
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    pad = int(0.04 * max(x1 - x0, y1 - y0))
    crop = ["-crop", f"{x1 - x0 + 2 * pad}x{y1 - y0 + 2 * pad}+{x0 - pad}+{y0 - pad}", "+repage"]
    style = Style(size[0] / 1100)
    tb = board["title_block"]
    caption = "  ·  ".join(x for x in (tb["title"] or pcb.stem, f"rev. {tb['rev']}" if tb["rev"] else "",
                                         board.get("note", "")) if x)
    band = style.px(64)
    w, h = size
    k = 3  # the PS2 version is drawn at a third of the size, then enlarged by whole pixels

    def finish(i_png):
        i, png = i_png
        plain, clear, ps2 = (work / f"{v}-{i:03d}.png" for v in ("plain", "clear", "ps2"))
        run(["magick", str(png), "-background", "none", *crop,
             "-resize", f"{w}x{h - band}", "-background", PAPER, "-gravity", "center",
             "-extent", f"{w}x{h - band}",
             "(", "-size", f"{w}x{band}", f"xc:{PAPER}",
             "-font", style.regular, "-pointsize", str(style.px(30)), "-fill", GREY,
             "-gravity", "center", "-annotate", "+0+0", caption, ")",
             "-append", "-flatten", "+repage", str(plain)], f"framing frame {i + 1}")
        # GIF transparency is all or nothing: the shadow on the floor goes, the board stays.
        run(["magick", str(png), "-background", "none", *crop, "-resize", f"{w}x{h}",
             "-gravity", "center", "-extent", f"{w}x{h}",
             "-channel", "A", "-threshold", "50%", "+channel", "+repage", str(clear)],
            f"cutting out frame {i + 1}")
        run(["magick", "-size", f"{w // k}x{h // k}", f"gradient:{VIEWER_TOP}-{VIEWER_BOTTOM}",
             "(", str(png), "-background", "none", *crop, "-resize", f"{w // k}x{h // k}", ")",
             "-gravity", "center", "-compose", "over", "-composite",
             # 32 levels a channel with a 4x4 ordered dither: the PS2's 15-bit frame buffer.
             "-ordered-dither", "o4x4,32", "-scale", f"{k * 100}%", "+repage", str(ps2)],
            f"pixelating frame {i + 1}")
        return plain, clear, ps2

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
        done = list(ex.map(finish, enumerate(frames)))
    delay = round(100 * SPIN_SECONDS / n)
    return [gif([d[0] for d in done], out, delay),
            gif([d[1] for d in done], out.with_name(f"{out.stem}-clear.gif"), delay, transparent=True),
            gif([d[2] for d in done], out.with_name(f"{out.stem}-ps2.gif"), delay)]


def layer_label(name, alias):
    label = name if not alias or alias == name else f"{name} ({alias})"
    return label + (", seen from below" if name == "B.Cu" else "")


def layer_caption(name, alias, tracks, nets, length=None):
    parts = [layer_label(name, alias),
             f"{tracks} track{'s' * (tracks != 1)}" + (f", {mm_text(length)}" if tracks and length else "")]
    if nets:
        parts.append(f"plane{'s' * (len(nets) > 1)} {', '.join(nets)}")
    return "  ·  ".join(parts)


def frame_box(boxes, margin=1.0):
    """The box both sheets of copper are framed on: the union of the outlines, plus a margin."""
    boxes = [b for b in boxes if b]
    if not boxes:
        raise Failure("no board outline (Edge.Cuts) found")
    return (min(b[0] for b in boxes) - margin, min(b[1] for b in boxes) - margin,
            max(b[2] for b in boxes) + margin, max(b[3] for b in boxes) + margin)


def copper_png(board_file, name, box, width, png, black_and_white=False, outline=True):
    """One copper layer and the outline, framed on `box` (mm), `width` pixels wide. Not
    mirrored: a point (x, y) of the board is at ((x - box[0]) * scale, (y - box[1]) * scale)."""
    svg = png.with_suffix(".svg")
    run(["kicad-cli", "pcb", "export", "svg", "--layers", f"{name},Edge.Cuts" if outline else name,
         "--mode-single", "--exclude-drawing-sheet", "--drill-shape-opt", "2",
         *(["--black-and-white"] if black_and_white else []),
         "-o", str(svg), str(board_file)], f"exporting {name}")
    crop_svg(svg, box)
    height = max(1, round(width * (box[3] - box[1]) / (box[2] - box[0])))
    density = max(150, min(2400, int(2 * width / ((box[2] - box[0]) / 25.4))))
    run(["magick", "-density", str(density), "-background", "white", str(svg), "-flatten",
         "-resize", f"{width}x{height}!",
         *(["-colorspace", "gray", "-threshold", "50%"] if black_and_white else []),
         "+repage", str(png)], f"rasterising {name}")
    return png


def layer_cell(box, width):
    """Tile size for a layer `width` pixels wide: the layer fills the tile's width (see tile())."""
    ratio = (box[3] - box[1]) / max(box[2] - box[0], 1e-6)
    return width, int(width * 0.92 * min(max(ratio, 0.4), 1.6) / 0.86)


def unfilled_copy(text, pcb, directory):
    """A copy of a board without its pour fills, next to its project file: on a board with a
    ground plane the copper covers everything, and the tracks disappear in it."""
    directory.mkdir(parents=True, exist_ok=True)
    copy = directory / pcb.name
    copy.write_text(strip_fills(text), encoding="utf-8")
    pro = pcb.with_suffix(".kicad_pro")
    if pro.exists():
        shutil.copy(pro, directory / pro.name)
    return copy


def mark(png, points, box, severity_colours, radius):
    """Rings around DRC violations, drawn on a layer picture framed on `box`: thick for
    errors, thin for warnings, errors on top."""
    if not points:
        return
    w = int(run(["magick", "identify", "-format", "%w", str(png)], "identify"))
    k = w / (box[2] - box[0])
    draw = []
    for x, y, severity in sorted(set(points), key=lambda p: p[2] == "error"):
        px, py = (x - box[0]) * k, (y - box[1]) * k
        width = max(2, radius // 4) if severity == "error" else max(1, radius // 8)
        draw += ["-stroke", severity_colours.get(severity, severity_colours["error"]),
                 "-strokewidth", str(width),
                 "-draw", f"circle {px:.1f},{py:.1f} {px + radius:.1f},{py:.1f}"]
    run(["magick", str(png), "-fill", "none", *draw, str(png)], "marking DRC violations")


DRC_COLOURS = {"error": "#e11d48", "warning": "#f59e0b"}


def make_layers(pcb, board, out, fast, work):
    layers = board["layers"]
    if not layers:
        raise Failure("no copper layer found in the board file")
    board_copy = unfilled_copy(board["text"], pcb, work / "unfilled")
    width = 750 if fast else 1500
    box = frame_box([board["box"]])
    style = Style(width / 1500 * 0.85)
    cell = layer_cell(box, width)
    drc = board.get("drc")

    def export(index_layer):
        i, (name, alias) = index_layer
        png = copper_png(board_copy, name, box, width, work / f"layer-{i}.png")
        if drc:
            mark(png, [(x, y, sev) for x, y, sev, where in drc["markers"] if where is None or name in where],
                 box, DRC_COLOURS, max(6, width // 90))
        if name == "B.Cu":
            run(["magick", str(png), "-flop", str(png)], "mirroring B.Cu")
        caption = layer_caption(name, alias, board["tracks"][name], board["planes"][name], board["length"][name])
        return tile(png, work / f"layer-tile-{i}.png", caption, cell, style, trim=False)

    with ThreadPoolExecutor(max_workers=min(len(layers), 4)) as ex:
        tiles = list(ex.map(export, enumerate(layers)))
    columns = 2 if len(layers) <= 4 else math.ceil(math.sqrt(len(layers)))
    details = board_details(board) + stats_details(board)
    if drc and drc["markers"]:
        details.append("DRC errors ringed in red, warnings in orange")
    return sheet(tiles, columns, header(pcb, board, "copper layers, pours hidden", details), out, style, work)


# --- design rule check -----------------------------------------------------------

def run_drc(pcb, board, work):
    """kicad-cli's DRC on the board: a summary for the headers, and where each violation is.
    A violation names its layer in its description ("... on F.Cu", or the layer's user name);
    one that names none is marked on every layer."""
    report = work / "drc.json"
    run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-error", "--severity-warning",
         "--units", "mm", "-o", str(report), str(pcb)], "running the DRC")
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Failure(f"unreadable DRC report: {e}")
    names = {alias or n: n for n, alias in board["layers"]} | {n: n for n, _ in board["layers"]}
    counts, markers = {"error": 0, "warning": 0}, []
    for v in data.get("violations", []) + data.get("unconnected_items", []):
        severity = v.get("severity", "error")
        counts[severity] = counts.get(severity, 0) + 1
        for item in v.get("items", [])[:1]:
            pos = item.get("pos") or {}
            if "x" not in pos:
                continue
            where = {names[m] for m in re.findall(r" on ([\w.&]+)", item.get("description", "")) if m in names}
            markers.append((float(pos["x"]), float(pos["y"]), severity, where or None))
    unconnected = len(data.get("unconnected_items", []))
    errors, warnings = counts.get("error", 0), counts.get("warning", 0)
    summary = ("DRC clean" if not errors and not warnings else
               f"DRC: {errors} error{'s' * (errors != 1)}, {warnings} warning{'s' * (warnings != 1)}"
               + (f", {unconnected} unconnected" if unconnected else ""))
    return {"summary": summary, "errors": errors, "warnings": warnings, "markers": markers}


def run_bytes(cmd, what):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        raise Failure(f"{what} failed ({cmd[0]} exit {r.returncode}):\n"
                      f"{r.stderr.decode(errors='replace')[-800:]}")
    return r.stdout


def older_version(pcb, rev, work):
    """The board's project directory at a git revision, unpacked: its board, its schematic,
    and its own 3D models when they live in the project. And a label: short hash and date."""
    if not shutil.which("git"):
        raise Failure("--diff needs git")
    top = Path(run(["git", "-C", str(pcb.parent), "rev-parse", "--show-toplevel"],
                   "finding the git repository").strip()).resolve()
    rel = pcb.parent.resolve().relative_to(top).as_posix()
    tar = run_bytes(["git", "-C", str(top), "archive", "--format=tar", rev, "--", rel or "."],
                    f"reading {rel or 'the repository'} at {rev}")
    dest = work / "older"
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        if hasattr(tarfile, "data_filter"):
            t.extractall(dest, filter="data")
        else:  # Python older than 3.12: git writes plain files and directories only
            t.extractall(dest)
    directory = dest / rel if rel else dest
    old_pcb = directory / pcb.name
    if not old_pcb.exists():
        raise Failure(f"{pcb.name} did not exist at {rev}")
    label = run(["git", "-C", str(top), "log", "-1", "--format=%h, %cs", rev, "--"],
                "reading the revision").strip()
    return {"rev": rev, "pcb": old_pcb, "board": read_board(old_pcb),
            "sch": old_pcb.with_suffix(".kicad_sch"),
            "label": f"{rev} ({label})" if label and not label.startswith(rev) else rev}


def crop_svg(svg, box):
    """Frame an SVG on `box` (mm, page coordinates): both versions then line up exactly."""
    x0, y0, x1, y1 = box
    text = svg.read_text(encoding="utf-8")
    text, n = re.subn(r'width="[\d.]+mm" height="[\d.]+mm" viewBox="[^"]*"',
                      f'width="{x1 - x0:.4f}mm" height="{y1 - y0:.4f}mm" '
                      f'viewBox="{x0:.4f} {y0:.4f} {x1 - x0:.4f} {y1 - y0:.4f}"', text, count=1)
    if not n:
        raise Failure("unexpected SVG from kicad-cli: no page size in mm")
    svg.write_text(text, encoding="utf-8")


# One pixel class each: ink only in the old version, only in the new one, in both.
REMOVED, ADDED, KEPT = "#d0342c", "#2e9e4a", "#a3a8b0"
REMOVED_HALO, ADDED_HALO = "#f7d9d6", "#d5efdc"


def hexrgb(h):
    return tuple(int(h[j:j + 2], 16) / 255 for j in (1, 3, 5))


def compare_masks(a, b, png, radius, flop=False):
    """Two black-on-white pictures of the same size to one: grey where both have ink, red
    where only the old one (`a`) has, green where only the new one has, and a pale halo
    around every change, since a removed 2 mm track is otherwise a few red pixels.
    Returns True when anything changed."""
    halos = []
    for tag, expr in (("removed", "u.r<0.5 && v.r>=0.5"), ("added", "u.r>=0.5 && v.r<0.5")):
        halo = png.with_name(f"{png.stem}-halo-{tag}.png")
        run(["magick", str(a), str(b), "-fx", f"{expr} ? 1 : 0",
             "-morphology", "Dilate", f"Disk:{radius}", str(halo)], "finding the changes")
        halos.append(halo)
    changed = any(float(run(["magick", str(h), "-format", "%[fx:maxima]", "info:"], "measuring")) > 0
                  for h in halos)
    run(["magick", *map(str, halos), "-compose", "lighten", "-composite", *(["-flop"] if flop else []),
         str(png.with_name(f"{png.stem}-changes.png"))], "collecting the changes")
    r, g, k, pr, pg = map(hexrgb, (REMOVED, ADDED, KEPT, REMOVED_HALO, ADDED_HALO))

    def pick(c):
        return (f"(wasc&&isc) ? {k[c]:.3f} : (wasc ? {r[c]:.3f} : (isc ? {g[c]:.3f} : "
                f"(nearr ? {pr[c]:.3f} : (neara ? {pg[c]:.3f} : 1))))")
    run(["magick", str(a), str(b), *map(str, halos), "-colorspace", "sRGB", "-type", "TrueColor",
         # wasc / isc: ink in the old / new version (single letters are taken by -fx).
         "-fx", "wasc=u[0].r<0.5; isc=u[1].r<0.5; nearr=u[2].r>0.5; neara=u[3].r>0.5; "
                f"channel({pick(0)}, {pick(1)}, {pick(2)})",
         *(["-flop"] if flop else []), "+repage", str(png)], "comparing")
    return changed


def blank(png, width, height):
    run(["magick", "-size", f"{width}x{height}", "xc:white", str(png)], "drawing an empty picture")
    return png


def make_diff(pcb, board, out, fast, work, old):
    rev, label, older = old["rev"], old["label"], old["board"]
    names = [n for n, _ in board["layers"]]
    names += [n for n, _ in older["layers"] if n not in names]
    aliases = dict(older["layers"]) | dict(board["layers"])
    box = frame_box([board["box"], older["box"]])
    versions = {"old": (unfilled_copy(older["text"], old["pcb"], work / "old"), dict(older["layers"])),
                "new": (unfilled_copy(board["text"], pcb, work / "new"), dict(board["layers"]))}
    width = 750 if fast else 1500
    height = max(1, round(width * (box[3] - box[1]) / (box[2] - box[0])))
    style = Style(width / 1500 * 0.85)
    cell = layer_cell(box, width)
    new_tracks, new_vias = track_keys(board["items"])
    old_tracks, old_vias = track_keys(older["items"])

    def raster(i, tag, name):
        path, present = versions[tag]
        png = work / f"{tag}-{i}.png"
        if name not in present:
            return blank(png, width, height)
        return copper_png(path, name, box, width, png, black_and_white=True)

    def compare(index_name):
        i, name = index_name
        png = work / f"diff-{i}.png"
        compare_masks(raster(i, "old", name), raster(i, "new", name), png, max(4, width // 120),
                      flop=name == "B.Cu")
        nt, ot = new_tracks.get(name, set()), old_tracks.get(name, set())
        plus, minus = len(nt - ot), len(ot - nt)
        caption = f"{layer_label(name, aliases.get(name))}  ·  " + (
            f"+{plus} / −{minus} tracks" if plus or minus else "no track changed")
        return tile(png, work / f"diff-tile-{i}.png", caption, cell, style, trim=False), plus, minus

    with ThreadPoolExecutor(max_workers=min(len(names), 4)) as ex:
        results = list(ex.map(compare, enumerate(names)))
    plus = sum(r[1] for r in results)
    minus = sum(r[2] for r in results)
    vplus, vminus = len(new_vias - old_vias), len(old_vias - new_vias)
    counts = ([f"+{plus} / −{minus} tracks", f"+{vplus} / −{vminus} vias"]
              if plus or minus or vplus or vminus else ["no track or via changed"])
    details = [f"changes since {label}", *counts, "red removed, green added, grey unchanged"]
    say(f"since {rev}: +{plus} / -{minus} tracks, +{vplus} / -{vminus} vias")
    columns = 2 if len(names) <= 4 else math.ceil(math.sqrt(len(names)))
    return sheet([r[0] for r in results], columns,
                 header(pcb, board, f"copper changes since {rev}", details), out, style, work)


def schematic_pages(sch, directory):
    """Every page of a schematic, hierarchy included, as black-and-white SVGs: name -> path."""
    directory.mkdir(parents=True, exist_ok=True)
    if not sch.exists():
        return {}
    run(["kicad-cli", "sch", "export", "svg", "--black-and-white", "-o", str(directory), str(sch)],
        f"exporting {sch.name}")
    return {p.stem: p for p in sorted(directory.glob("*.svg"))}


def make_sch_diff(pcb, board, out, fast, work, old):
    sch = pcb.with_suffix(".kicad_sch")
    new_pages = schematic_pages(sch, work / "sch-new")
    old_pages = schematic_pages(old["sch"], work / "sch-old")
    names = list(new_pages) + [n for n in old_pages if n not in new_pages]
    if not names:
        raise Failure(f"no schematic {sch.name} next to the board, now or at {old['rev']}")
    width = 1000 if fast else 2000
    style = Style(width / 1500 * 0.85)
    sizes = {}

    def page_size(svg):
        m = re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', svg.read_text(encoding="utf-8")[:4000])
        return (float(m.group(1)), float(m.group(2))) if m else (297.0, 210.0)

    for n in names:
        sizes[n] = page_size(new_pages.get(n) or old_pages[n])
    ratio = max(h / w for w, h in sizes.values())
    cell = (width, int(width * 0.92 * ratio / 0.86))

    def raster(svg, png, w_mm, h_mm):
        height = round(width * h_mm / w_mm)
        if svg is None:
            return blank(png, width, height)
        run(["magick", "-density", str(int(2 * width / (w_mm / 25.4))), "-background", "white", str(svg),
             "-flatten", "-resize", f"{width}x{height}!", "-colorspace", "gray", "-threshold", "60%",
             "+repage", str(png)], f"rasterising {svg.name}")
        return png

    def compare(index_name):
        i, name = index_name
        w_mm, h_mm = sizes[name]
        a = raster(old_pages.get(name), work / f"sch-old-{i}.png", w_mm, h_mm)
        b = raster(new_pages.get(name), work / f"sch-new-{i}.png", w_mm, h_mm)
        png = work / f"sch-diff-{i}.png"
        changed = compare_masks(a, b, png, max(4, width // 100))
        title = name[len(pcb.stem) + 1:] if name.startswith(pcb.stem + "-") else "root sheet"
        state = ("added" if name not in old_pages else "removed" if name not in new_pages
                 else "changed" if changed else "no change")
        tiles = [tile(png, work / f"sch-tile-{i}.png", f"{title}  ·  {state}", cell, style, trim=False)]
        if state == "changed":
            # A value edited on an A4 page is a few pixels at this scale: the same page,
            # cropped on what changed.
            box = opaque_box_of(png.with_name(f"{png.stem}-changes.png"))
            if box:
                # Drawn again from the SVGs, cropped on what changed: enlarging the page's
                # pixels would only give bigger blurs.
                k = w_mm / width  # mm a pixel
                x0, y0, x1, y1 = (v * k for v in box)
                side = max(x1 - x0, (y1 - y0) * cell[0] / cell[1], 40.0) * 1.3
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                region = (cx - side / 2, cy - side * cell[1] / cell[0] / 2,
                          cx + side / 2, cy + side * cell[1] / cell[0] / 2)
                parts = []
                for tag, svg in (("old", old_pages.get(name)), ("new", new_pages.get(name))):
                    out_png = work / f"sch-zoom-{tag}-{i}.png"
                    copy = work / f"sch-zoom-{tag}-{i}.svg"
                    copy.write_text(svg.read_text(encoding="utf-8"), encoding="utf-8")
                    crop_svg(copy, region)
                    zh = round(cell[0] * (region[3] - region[1]) / (region[2] - region[0]))
                    run(["magick", "-density", str(int(2 * cell[0] / (side / 25.4))), "-background", "white",
                         str(copy), "-flatten", "-resize", f"{cell[0]}x{zh}!", "-colorspace", "gray",
                         "-threshold", "60%", "+repage", str(out_png)], "rasterising the zoom")
                    parts.append(out_png)
                zoom = work / f"sch-zoom-{i}.png"
                compare_masks(parts[0], parts[1], zoom, max(4, cell[0] // 80))
                tiles.append(tile(zoom, work / f"sch-zoom-tile-{i}.png", f"{title}  ·  zoom on the changes",
                                  cell, style, trim=False))
        return tiles, state

    with ThreadPoolExecutor(max_workers=min(len(names), 4)) as ex:
        results = list(ex.map(compare, enumerate(names)))
    touched = sum(1 for _, st in results if st != "no change")
    results = [(t, st) for tiles, st in results for t in tiles]
    touched_pages = touched
    details = [f"changes since {old['label']}",
               f"{touched_pages} of {len(names)} sheet{'s' * (len(names) != 1)} changed" if touched_pages else "no sheet changed",
               "red removed, green added, grey unchanged"]
    return sheet([r[0] for r in results], 2 if len(results) > 1 else 1,
                 header(pcb, board, f"schematic changes since {old['rev']}", details), out, style, work)


def before_after_views(pcb, old, views, size, quality, work):
    """The same views of the board now and at the older revision, rendered side by side."""
    jobs = [(tag, path, view) for view in views for tag, path in (("old", old["pcb"]), ("new", pcb))]
    with ThreadPoolExecutor(max_workers=min(len(jobs), os.cpu_count() or 2)) as ex:
        pngs = list(ex.map(lambda j: render_view(j[1], j[2], size, quality, work / f"{j[0]}-{j[2][0]}.png"),
                           jobs))
    return {(tag, view[0]): png for (tag, _, view), png in zip(jobs, pngs)}


def ring_changes(before, after, radius):
    """Ring in red, on the `after` render, the places that look different from `before`.
    Same board outline, same camera: the two renders line up pixel for pixel. Returns True
    when something was ringed."""
    if run(["magick", "identify", "-format", "%wx%h", str(before)], "identify") != \
            run(["magick", "identify", "-format", "%wx%h", str(after)], "identify"):
        return False
    mask = after.with_name(f"{after.stem}-changes.png")
    run(["magick", str(before), str(after), "-background", "white", "-alpha", "remove",
         "-compose", "difference", "-composite", "-colorspace", "gray", "-threshold", "12%",
         # Opening drops the odd pixel of noise; dilating joins a moved part into one blob.
         "-morphology", "Open", "Disk:1", "-morphology", "Dilate", f"Disk:{radius}", str(mask)],
        "finding what changed")
    if float(run(["magick", str(mask), "-format", "%[fx:maxima]", "info:"], "measuring")) == 0:
        return False
    ring = after.with_name(f"{after.stem}-ring.png")
    run(["magick", str(mask), "-morphology", "EdgeOut", f"Disk:{max(2, radius // 3)}",
         "-background", "black", "-alpha", "shape", "-fill", REMOVED, "-colorize", "100", str(ring)],
        "drawing the rings")
    run(["magick", str(after), str(ring), "-compose", "over", "-composite", str(after)], "ringing changes")
    return True


def make_3d_diff(pcb, board, out, fast, work, old):
    size = (900, 650) if fast else (1800, 1300)
    cell = (600, 430) if fast else (1100, 800)
    style = Style(cell[0] / 1100)
    views = VIEWS[:2]
    pngs = before_after_views(pcb, old, views, size, "basic" if fast else "high", work)
    marked = 0
    for name, _, _ in views:
        if ring_changes(pngs[("old", name)], pngs[("new", name)], max(6, size[0] // 90)):
            marked += 1
    tiles = []
    for name, caption, _ in views:
        for tag, when in (("old", f"at {old['label']}"), ("new", "now")):
            tiles.append(tile(pngs[(tag, name)], work / f"3d-diff-{tag}-{name}.png",
                              f"{caption}  ·  {when}", cell, style, trim=True))
    details = [f"before: {old['label']}", "after: the board as it is now",
               "what changed ringed in red" if marked else "no visible change"]
    return sheet(tiles, 2, header(pcb, board, f"3D before and after {old['rev']}", details), out, style, work)


def make_fade(pcb, board, out, fast, work, old):
    """A GIF that goes from the older board to the current one and back, so a change blinks."""
    size = (640, 480) if fast else (1000, 750)
    pngs = before_after_views(pcb, old, VIEWS[:1], (int(size[0] * 1.25), int(size[1] * 1.25)),
                              "basic" if fast else "high", work)
    a, b = pngs[("old", "1")], pngs[("new", "1")]
    boxes = [opaque_box(a), opaque_box(b)]
    boxes = [x for x in boxes if x]
    if not boxes:
        raise Failure("kicad-cli rendered nothing")
    x0, y0 = min(x[0] for x in boxes), min(x[1] for x in boxes)
    x1, y1 = max(x[2] for x in boxes), max(x[3] for x in boxes)
    pad = int(0.04 * max(x1 - x0, y1 - y0))
    crop = f"{x1 - x0 + 2 * pad}x{y1 - y0 + 2 * pad}+{x0 - pad}+{y0 - pad}"
    style = Style(size[0] / 1100)
    band = style.px(64)
    w, h = size
    framed = {}
    for tag, png, text in (("old", a, f"before  ·  {old['label']}"), ("new", b, "now")):
        framed[tag] = work / f"fade-{tag}.png"
        run(["magick", str(png), "-background", "none", "-crop", crop, "+repage",
             "-resize", f"{w}x{h - band}", "-background", PAPER, "-gravity", "center",
             "-extent", f"{w}x{h - band}",
             "(", "-size", f"{w}x{band}", f"xc:{PAPER}", "-font", style.regular,
             "-pointsize", str(style.px(30)), "-fill", GREY, "-gravity", "center",
             "-annotate", "+0+0", text, ")", "-append", "-flatten", "+repage", str(framed[tag])],
            "framing a view")
    steps = 10
    blends = {}
    for i in range(1, steps):
        blends[i] = work / f"fade-{i:02d}.png"
        run(["magick", str(framed["new"]), str(framed["old"]), "-compose", "blend",
             "-define", f"compose:args={100 * i // steps}", "-composite", str(blends[i])], "blending")
    seq = (["-delay", "150", str(framed["old"]), "-delay", "6", *[str(blends[i]) for i in range(1, steps)],
            "-delay", "150", str(framed["new"]), "-delay", "6",
            *[str(blends[i]) for i in range(steps - 1, 0, -1)]])
    run(["magick", *seq, "-dither", "None", "+remap", "-loop", "0", "-layers", "Optimize", str(out)],
        f"assembling {out.name}")
    return out


# --- one net ----------------------------------------------------------------------

LAYER_COLOURS = {"F.Cu": "#c83434", "B.Cu": "#4d7fc4", "In1.Cu": "#7fc87f", "In2.Cu": "#ce7d2c",
                 "In3.Cu": "#4fcbcb", "In4.Cu": "#db628b", "In5.Cu": "#a7a5c6", "In6.Cu": "#28cccc",
                 "In7.Cu": "#e8b2a7", "In8.Cu": "#c9b54f"}
OTHER_COPPER = "#d5d8dd"


def board_nets(board):
    names = set(board["netmap"].values())
    names.update(re.findall(r'\(net (?:\d+ )?"((?:[^"\\]|\\.)+)"\)', board["text"]))
    return {n for n in names if n}


def find_net(board, query):
    """The net named `query`: exactly, then ignoring case, then by its last path component
    (`VBAT` for `/Solar_module/VBAT`) when only one net ends that way."""
    names = board_nets(board)
    if query in names:
        return query
    for test in (lambda n: n.casefold() == query.casefold(),
                 lambda n: n.rsplit("/", 1)[-1].casefold() == query.casefold()):
        found = sorted(n for n in names if test(n))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise Failure(f"several nets match {query}: {', '.join(found[:8])}")
    close = difflib.get_close_matches(query, sorted(names), n=6, cutoff=0.4)
    raise Failure(f"no net {query} on this board" + (f"; close: {', '.join(close)}" if close else ""))


def only_net(text, netmap, net):
    """The board with no copper but the net's own: its tracks, vias, pads and zones (with
    their fill). Everything else stays: outline, footprints' other layers."""
    out, pos = [], 0
    for head, a, b in top_level_spans(text):
        item = text[a:b]
        keep = item
        if head in ("segment", "arc", "via", "zone"):
            keep = item if item_net(item, netmap) == net else ""
        elif head.startswith("gr_") and re.search(r'\(layers? "[^"]*\.Cu"', item):
            keep = ""
        elif head == "footprint":
            keep = footprint_only_net(item, netmap, net)
        out.append(text[pos:a])
        out.append(keep)
        pos = b
    out.append(text[pos:])
    return "".join(out)


def footprint_only_net(item, netmap, net):
    out, pos, i = [], 0, 0
    while True:
        i = item.find("(pad ", i)
        if i < 0:
            break
        end = closing_paren(item, i) + 1
        if item_net(item[i:end], netmap) != net:
            out.append(item[pos:i])
            pos = end
        i = end
    out.append(item[pos:])
    return "".join(out)


def make_net(pcb, board, out, fast, work, query):
    net = find_net(board, query)
    layers = board["layers"]
    width = 750 if fast else 1500
    box = frame_box([board["box"]])
    style = Style(width / 1500 * 0.85)
    cell = layer_cell(box, width)
    base = unfilled_copy(board["text"], pcb, work / "all")
    mine = work / "net" / pcb.name
    mine.parent.mkdir()
    mine.write_text(only_net(board["text"], board["netmap"], net), encoding="utf-8")
    pro = pcb.with_suffix(".kicad_pro")
    if pro.exists():
        shutil.copy(pro, mine.parent / pro.name)

    counts, lengths, vias, pads = {}, {}, 0, 0
    for head, t in board["items"]:
        if head in ("segment", "arc") and item_net(t, board["netmap"]) == net:
            layer = re.search(r'\(layer "([^"]+)"\)', t)
            if layer:
                counts[layer.group(1)] = counts.get(layer.group(1), 0) + 1
                lengths[layer.group(1)] = lengths.get(layer.group(1), 0.0) + track_length(head, t)
        elif head == "via" and item_net(t, board["netmap"]) == net:
            vias += 1
        elif head == "footprint":
            pads += sum(1 for p in re.finditer(r"\(pad ", t)
                        if item_net(t[p.start():closing_paren(t, p.start()) + 1], board["netmap"]) == net)
    other = hexrgb(OTHER_COPPER)

    def draw(index_layer):
        i, (name, alias) = index_layer
        a = copper_png(base, name, box, width, work / f"all-{i}.png", black_and_white=True)
        b = copper_png(mine, name, box, width, work / f"net-{i}.png", black_and_white=True, outline=False)
        c = hexrgb(LAYER_COLOURS.get(name, "#c83434"))
        pick = lambda k: f"(isnet) ? {c[k]:.3f} : ((iscu) ? {other[k]:.3f} : 1)"
        png = work / f"net-colour-{i}.png"
        run(["magick", str(a), str(b), "-colorspace", "sRGB", "-type", "TrueColor",
             "-fx", f"iscu=u.r<0.5; isnet=v.r<0.5; channel({pick(0)}, {pick(1)}, {pick(2)})",
             *(["-flop"] if name == "B.Cu" else []), "+repage", str(png)], f"drawing {net} on {name}")
        n = counts.get(name, 0)
        caption = f"{layer_label(name, alias)}  ·  " + (
            f"{n} track{'s' * (n != 1)}, {mm_text(lengths[name])}" if n else "no track")
        return tile(png, work / f"net-tile-{i}.png", caption, cell, style, trim=False)

    with ThreadPoolExecutor(max_workers=min(len(layers), 4)) as ex:
        tiles = list(ex.map(draw, enumerate(layers)))
    total = sum(counts.values())
    details = [f"net {net}", f"{total} track{'s' * (total != 1)}, {mm_text(sum(lengths.values()))}",
               f"{vias} via{'s' * (vias != 1)}", f"{pads} pad{'s' * (pads != 1)}",
               "the rest of the copper in grey"]
    columns = 2 if len(layers) <= 4 else math.ceil(math.sqrt(len(layers)))
    return sheet(tiles, columns, header(pcb, board, f"net {net}", details), out, style, work)


def slug(name):
    return re.sub(r"[^\w.+-]+", "_", name).strip("_") or "net"


# --- opening -------------------------------------------------------------------

def say(msg):
    print(f"kishot: {msg}", file=sys.stderr)


def hyprland_lua():
    """True when Hyprland runs with its Lua configuration, False for hyprlang."""
    r = subprocess.run(["hyprctl", "eval", "return 1"], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "ok"


def lua_string(s):
    level = 0
    while f"]{'=' * level}]" in s:
        level += 1
    return f"[{'=' * level}[{s}]{'=' * level}]"


def hypr_clients():
    r = subprocess.run(["hyprctl", "-j", "clients"], capture_output=True, text=True)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else []
    except ValueError:
        return []


def viewer_windows(path):
    return [c for c in hypr_clients()
            if c.get("class", "").lower() == "swayimg" and path.name in c.get("title", "")]


def open_on_workspace(paths, workspace):
    """swayimg on a Hyprland workspace, in the background; an older window of the
    same image is closed first, so the new picture replaces it."""
    lua = hyprland_lua()

    def hypr(lua_call, classic):
        cmd = ["hyprctl", "eval", f"hl.dispatch({lua_call})"] if lua else ["hyprctl", "dispatch", *classic]
        subprocess.run(cmd, capture_output=True)

    for path in paths:
        # Each swayimg window is its own process: ending it closes the window,
        # whatever the Hyprland version or configuration language.
        for c in viewer_windows(path):
            try:
                os.kill(c["pid"], signal.SIGTERM)
            except (KeyError, OSError):
                pass
        for _ in range(20):
            if not viewer_windows(path):
                break
            time.sleep(0.1)
        cmd = f"[workspace {workspace} silent] swayimg {shlex.quote(str(path))}"
        hypr(f"hl.dsp.exec_cmd({lua_string(cmd)})", ["exec", cmd])
        # The rule is not always honoured: move the window there if needed.
        for _ in range(30):
            wins = viewer_windows(path)
            if wins:
                for c in wins:
                    if str(c.get("workspace", {}).get("name")) != str(workspace):
                        a = c["address"]
                        hypr(f"hl.dsp.window.move({{workspace='{workspace}', follow=false, window='address:{a}'}})",
                             ["movetoworkspacesilent", f"{workspace},address:{a}"])
                break
            time.sleep(0.1)
        else:
            say(f"no swayimg window showed up for {path.name}")


def open_images(paths, viewer, workspace):
    if workspace and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") \
            and shutil.which("hyprctl") and shutil.which("swayimg") and not viewer:
        open_on_workspace(paths, workspace)
        return
    if workspace and not viewer:
        say("--workspace needs Hyprland, hyprctl and swayimg; opening normally")
    cmd = shlex.split(viewer) if viewer else None
    if not cmd:
        for candidate in ("swayimg", "xdg-open", "open"):
            if shutil.which(candidate):
                cmd = [candidate]
                break
    if not cmd:
        say("no image viewer found (swayimg, xdg-open); pass --viewer")
        return
    for path in paths:
        subprocess.Popen([*cmd, str(path)], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- command line --------------------------------------------------------------

EPILOG = """\
examples:
  kishot                            the 3D and copper sheets of the board here
  kishot board.kicad_pcb --fast     quick preview: seconds instead of a minute
  kishot hw/ -s layers -o /tmp      only the copper layers, written to /tmp
  kishot --drc --note "for review"  DRC results and a note on every sheet
  kishot --net GND                  where one net runs, layer by layer
  kishot --diff HEAD~3              what changed since three commits ago
  kishot -s spin                    animated GIFs of the board turning, for chat
  kishot --open --workspace 11      open the images on Hyprland workspace 11

sheets:
  3d        six 3D views
  layers    every copper layer, pours hidden, with track counts and lengths
  net       one net (--net NAME) in its layer colour, the rest in grey
  diff      every copper layer against an older git revision (--diff REV):
            red removed, green added, grey unchanged
  sch-diff  the same for every page of the schematic
  3d-diff   top and bottom, before and after, side by side
  fade      a GIF going from the older board to the current one and back
  spin      the board turning once, three animated GIFs: on a light background
            with its name, transparent without text (-clear), and pixelated
            like a PS2 game (-ps2); a few minutes, 30 s with --fast
  Without -s: 3d and layers; with --net, the net sheet; with --diff, the four
  diff sheets (sch-diff only when there is a schematic). -s all: every sheet
  the options allow.

output:
  <board>-<sheet>.png, <board>-net-<net>.png, <board>-fade.gif,
  <board>-spin.gif, -spin-clear.gif and -spin-ps2.gif, in --out-dir; without
  it, in a fabrication/ or fab/ directory next to the board if there is one,
  else next to the board. Each path written is printed on stdout, one per line.

environment:
  KISHOT_WORKSPACE   default for --workspace
  KISHOT_VIEWER      default for --viewer

exit status: 0 when every sheet asked for was written, 1 otherwise, 2 on a
usage error.
"""


def find_board(arg):
    p = arg.resolve()
    if p.is_dir():
        boards = sorted(b for b in p.glob("*.kicad_pcb") if not b.name.startswith("_autosave"))
        if not boards:
            raise Failure(f"no .kicad_pcb file in {p}")
        if len(boards) > 1:
            raise Failure(f"{len(boards)} .kicad_pcb files in {p}: name the one to use")
        p = boards[0]
    if p.suffix != ".kicad_pcb" or not p.is_file():
        raise Failure(f"not a .kicad_pcb file: {p}")
    return p


def default_out_dir(pcb):
    for name in ("fabrication", "fab"):
        if (pcb.parent / name).is_dir():
            return pcb.parent / name
    return pcb.parent


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="kishot", description="Presentation sheets of a KiCad board: 3D views and copper layers.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", type=Path, nargs="?", default=Path("."),
                    help="a .kicad_pcb file, or a directory holding exactly one (default: .)")
    ap.add_argument("-s", "--sheet", choices=(*SHEETS, "all"), action="append",
                    help="sheet to make, repeatable (see below)")
    ap.add_argument("-d", "--diff", metavar="REV",
                    help="compare with the board at this git revision (a commit, tag, branch, "
                         "HEAD~1...)")
    ap.add_argument("-n", "--net", metavar="NAME",
                    help="the net to show (exact name, any case, or its last part: VBAT for "
                         "/power/VBAT)")
    ap.add_argument("--drc", action="store_true",
                    help="run KiCad's DRC: errors and warnings in every header, ringed on the "
                         "copper layers")
    ap.add_argument("--note", metavar="TEXT", help="a line of your own at the head of every sheet")
    ap.add_argument("-o", "--out-dir", type=Path, metavar="DIR",
                    help="where to write the images (created if missing)")
    ap.add_argument("-f", "--fast", action="store_true",
                    help="basic 3D quality and smaller images, for a preview")
    ap.add_argument("--open", action="store_true", help="open the images once written")
    ap.add_argument("--workspace", metavar="N", default=os.environ.get("KISHOT_WORKSPACE"),
                    help="with --open under Hyprland: show them with swayimg on workspace N, "
                         "without switching to it, replacing an older window of the same image")
    ap.add_argument("--viewer", metavar="CMD", default=os.environ.get("KISHOT_VIEWER"),
                    help="with --open: the viewer command (default: swayimg, else xdg-open)")
    ap.add_argument("-V", "--version", action="version", version=f"kishot {__version__}")
    return ap.parse_args(argv)


def choose_sheets(a):
    """The sheets to make, or a usage error."""
    for s in a.sheet or ():
        if s in NEEDS_DIFF and not a.diff:
            return f"the {s} sheet needs --diff REV"
        if s == "net" and not a.net:
            return "the net sheet needs --net NAME"
    if not a.sheet:
        wanted = set()
        if a.diff:
            wanted |= set(NEEDS_DIFF)
        if a.net:
            wanted.add("net")
        wanted = wanted or {"3d", "layers"}
    elif "all" in a.sheet:
        wanted = ({"3d", "layers", "spin"} | (set(NEEDS_DIFF) if a.diff else set())
                  | ({"net"} if a.net else set()))
    else:
        wanted = set(a.sheet)
    return [s for s in SHEETS if s in wanted]


def output_name(pcb, sheet, board, net):
    if sheet == "net":
        return f"{pcb.stem}-net-{slug(find_net(board, net))}.png"
    return f"{pcb.stem}-{sheet}.{'gif' if sheet in ('spin', 'fade') else 'png'}"


def main(argv=None):
    a = parse_args(argv)
    sheets = choose_sheets(a)
    if isinstance(sheets, str):
        print(f"kishot: error: {sheets}", file=sys.stderr)
        return 2
    try:
        pcb = find_board(a.board)
        for tool in ("kicad-cli", "magick"):
            if not shutil.which(tool):
                raise Failure(f"{tool} not found in PATH")
        out_dir = (a.out_dir or default_out_dir(pcb)).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        board = read_board(pcb)
        board["note"] = a.note or ""
        if a.net:
            find_net(board, a.net)  # fail now, before minutes of rendering
        written = []
        with tempfile.TemporaryDirectory(prefix="kishot-") as t:
            old = None
            if any(s in NEEDS_DIFF for s in sheets):
                old = older_version(pcb, a.diff, Path(t))
                if "sch-diff" in sheets and not a.sheet and not (
                        pcb.with_suffix(".kicad_sch").exists() or old["sch"].exists()):
                    sheets.remove("sch-diff")
            if a.drc:
                say("running the DRC…")
                drc_dir = Path(t) / "drc"
                drc_dir.mkdir()
                board["drc"] = run_drc(pcb, board, drc_dir)
                say(board["drc"]["summary"])
            for s in sheets:
                work = Path(t) / s
                work.mkdir()
                out = out_dir / output_name(pcb, s, board, a.net)
                say(f"{s} sheet of {pcb.name}…")
                if s in NEEDS_DIFF:
                    make = {"diff": make_diff, "sch-diff": make_sch_diff,
                            "3d-diff": make_3d_diff, "fade": make_fade}[s]
                    made = [make(pcb, board, out, a.fast, work, old)]
                elif s == "net":
                    made = [make_net(pcb, board, out, a.fast, work, a.net)]
                elif s == "spin":
                    made = make_spin(pcb, board, out, a.fast, work)
                else:
                    made = [(make_3d if s == "3d" else make_layers)(pcb, board, out, a.fast, work)]
                for path in made:
                    print(path, flush=True)
                written += made
    except Failure as e:
        say(str(e))
        return 1
    except KeyboardInterrupt:
        return 130
    if a.open:
        open_images(written, a.viewer, a.workspace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
