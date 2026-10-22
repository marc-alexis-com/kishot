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
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

__version__ = "0.3.0"

SHEETS = ("3d", "layers", "diff", "spin")

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


def top_level_items(s):
    """The direct children of the root expression, as (head, text) pairs."""
    items, depth, start = [], 0, None
    for m in TOKEN.finditer(s):
        t = m.group()
        if t == "(":
            depth += 1
            if depth == 2:
                start = m.start()
        elif t == ")":
            if depth == 2:
                text = s[start:m.end()]
                head = re.match(r"\(\s*([^\s()]+)", text)
                items.append((head.group(1) if head else "", text))
            depth -= 1
    return items


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
    tracks = {n: 0 for n, _ in layers}
    for head, t in items:
        if head in ("segment", "arc"):
            m = re.search(r'\(layer "([^"]+)"\)', t)
            if m and m.group(1) in tracks:
                tracks[m.group(1)] += 1
    return {
        "text": text,
        "title_block": tb,
        "box": box,
        "layers": layers,
        "tracks": tracks,
        "planes": planes(items, layers, box),
        "footprints": sum(1 for h, _ in items if h == "footprint"),
        "items": items,
    }


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


def header(pcb, board, subtitle, details=None):
    tb = board["title_block"]
    box = board["box"]
    n = len(board["layers"])
    details = [x for x in (
        f"rev. {tb['rev']}" if tb["rev"] else "",
        tb["date"], tb["company"],
        f"{box[2] - box[0]:.1f} × {box[3] - box[1]:.1f} mm" if box else "",
        f"{n} copper layer{'s' * (n != 1)}" if n else "",
        f"{board['footprints']} footprints") if x] if details is None else details
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


def make_3d(pcb, board, out, fast, work):
    size = (900, 650) if fast else (1800, 1300)
    cell = (600, 430) if fast else (1100, 800)
    quality = "basic" if fast else "high"
    style = Style(cell[0] / 1100)

    def render(view):
        name, caption, opts = view
        png = work / f"3d-{name}.png"
        zoom = float(opts[opts.index("--zoom") + 1]) if "--zoom" in opts else 1.0
        base = [o for i, o in enumerate(opts)
                if o != "--zoom" and (i == 0 or opts[i - 1] != "--zoom")]
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
        return tile(png, work / f"3d-tile-{name}.png", caption, cell, style, trim=True)

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


def make_spin(pcb, board, out, fast, work):
    """An animated GIF: the board, rendered by KiCad, turning once on itself."""
    n = 24 if fast else 36
    size = (480, 360) if fast else (800, 600)
    render = (int(size[0] * 1.5), int(size[1] * 1.5))  # cropped down afterwards
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
    crop = f"{x1 - x0 + 2 * pad}x{y1 - y0 + 2 * pad}+{x0 - pad}+{y0 - pad}"
    style = Style(size[0] / 1100)
    tb = board["title_block"]
    caption = "  ·  ".join(x for x in (tb["title"] or pcb.stem, f"rev. {tb['rev']}" if tb["rev"] else "") if x)
    band = style.px(64)

    def finish(i_png):
        i, png = i_png
        dst = work / f"spin-final-{i:03d}.png"
        run(["magick", str(png), "-background", "none", "-crop", crop, "+repage",
             "-resize", f"{size[0]}x{size[1] - band}", "-background", PAPER, "-gravity", "center",
             "-extent", f"{size[0]}x{size[1] - band}",
             "(", "-size", f"{size[0]}x{band}", f"xc:{PAPER}",
             "-font", style.regular, "-pointsize", str(style.px(30)), "-fill", GREY,
             "-gravity", "center", "-annotate", "+0+0", caption, ")",
             "-append", "-flatten", "+repage", str(dst)], f"framing frame {i + 1}")
        return dst

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
        finals = list(ex.map(finish, enumerate(frames)))
    # One palette for the whole animation, taken from a few frames: per-frame palettes
    # make the colours flicker as the board turns.
    palette = work / "palette.png"
    run(["magick", *map(str, finals[::max(1, n // 6)]), "+append", "-dither", "None",
         "-colors", "255", "-unique-colors", str(palette)], "building the palette")
    run(["magick", "-delay", str(round(100 * SPIN_SECONDS / n)), *map(str, finals),
         "-dither", "None", "-remap", str(palette), "-loop", "0", "-layers", "Optimize", str(out)],
        "assembling the animation")
    return out


def layer_caption(name, alias, tracks, nets):
    label = name if not alias or alias == name else f"{name} ({alias})"
    if name == "B.Cu":
        label += ", seen from below"
    parts = [label, f"{tracks} track{'s' * (tracks != 1)}"]
    if nets:
        parts.append(f"plane{'s' * (len(nets) > 1)} {', '.join(nets)}")
    return "  ·  ".join(parts)


def make_layers(pcb, board, out, fast, work):
    layers = board["layers"]
    if not layers:
        raise Failure("no copper layer found in the board file")
    # A copy of the board without its pour fills: on a board with a ground
    # plane the copper covers everything, and the tracks disappear in it.
    copy = work / "unfilled"
    copy.mkdir()
    board_copy = copy / pcb.name
    board_copy.write_text(strip_fills(board["text"]), encoding="utf-8")
    pro = pcb.with_suffix(".kicad_pro")
    if pro.exists():
        shutil.copy(pro, copy / pro.name)

    width = 750 if fast else 1500
    box = board["box"]
    inches = (box[2] - box[0]) / 25.4 if box else 4
    density = max(150, min(1200, int(2 * width / inches)))
    style = Style(width / 1500 * 0.85)
    # Tile height chosen so the layer fills the tile's width (see tile()).
    ratio = (box[3] - box[1]) / max(box[2] - box[0], 1e-6) if box else 0.8
    cell = (width, int(width * 0.92 * min(max(ratio, 0.4), 1.6) / 0.86))

    def export(index_layer):
        i, (name, alias) = index_layer
        svg = work / f"layer-{i}.svg"
        run(["kicad-cli", "pcb", "export", "svg", "--layers", f"{name},Edge.Cuts",
             "--mode-single", "--fit-page-to-board", "--exclude-drawing-sheet",
             "--drill-shape-opt", "2", *(["--mirror"] if name == "B.Cu" else []),
             "-o", str(svg), str(board_copy)], f"exporting {name}")
        png = work / f"layer-{i}.png"
        run(["magick", "-density", str(density), "-background", "white", str(svg),
             "-flatten", "-resize", f"{width}x", "+repage", str(png)], f"rasterising {name}")
        caption = layer_caption(name, alias, board["tracks"][name], board["planes"][name])
        return tile(png, work / f"layer-tile-{i}.png", caption, cell, style, trim=False)

    with ThreadPoolExecutor(max_workers=min(len(layers), 4)) as ex:
        tiles = list(ex.map(export, enumerate(layers)))
    columns = 2 if len(layers) <= 4 else math.ceil(math.sqrt(len(layers)))
    return sheet(tiles, columns, header(pcb, board, "copper layers, pours hidden"), out, style, work)


def git_version(pcb, rev):
    """The board's content at a git revision, and a label for it: short hash and date."""
    if not shutil.which("git"):
        raise Failure("--diff needs git")
    top = Path(run(["git", "-C", str(pcb.parent), "rev-parse", "--show-toplevel"],
                   "finding the git repository").strip())
    rel = pcb.resolve().relative_to(top.resolve()).as_posix()
    text = run(["git", "-C", str(top), "show", f"{rev}:{rel}"], f"reading {rel} at {rev}")
    label = run(["git", "-C", str(top), "log", "-1", "--format=%h, %cs", rev, "--"],
                "reading the revision").strip()
    return text, f"{rev} ({label})" if label and not label.startswith(rev) else rev


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


# One pixel class each: copper only in the old version, only in the new one, in both.
REMOVED, ADDED, KEPT = "#d0342c", "#2e9e4a", "#a3a8b0"
REMOVED_HALO, ADDED_HALO = "#f7d9d6", "#d5efdc"


def make_diff(pcb, board, out, fast, work, rev):
    old_text, label = git_version(pcb, rev)
    old = read_board(pcb, old_text)
    names = [n for n, _ in board["layers"]]
    names += [n for n, _ in old["layers"] if n not in names]
    aliases = dict(old["layers"]) | dict(board["layers"])
    boxes = [b for b in (board["box"], old["box"]) if b]
    if not boxes:
        raise Failure("no board outline (Edge.Cuts) in either version")
    box = (min(b[0] for b in boxes) - 1, min(b[1] for b in boxes) - 1,
           max(b[2] for b in boxes) + 1, max(b[3] for b in boxes) + 1)

    versions = {}
    for tag, b in (("old", old), ("new", board)):
        d = work / tag
        d.mkdir()
        (d / pcb.name).write_text(strip_fills(b["text"]), encoding="utf-8")
        pro = pcb.with_suffix(".kicad_pro")
        if pro.exists():
            shutil.copy(pro, d / pro.name)
        versions[tag] = (d / pcb.name, {n for n, _ in b["layers"]})

    width = 750 if fast else 1500
    density = max(150, min(2400, int(width / ((box[2] - box[0]) / 25.4))))
    style = Style(width / 1500 * 0.85)
    ratio = (box[3] - box[1]) / (box[2] - box[0])
    cell = (width, int(width * 0.92 * min(max(ratio, 0.4), 1.6) / 0.86))
    new_tracks, new_vias = track_keys(board["items"])
    old_tracks, old_vias = track_keys(old["items"])

    def raster(i, tag, name):
        path, present = versions[tag]
        png = work / f"{tag}-{i}.png"
        if name not in present:
            run(["magick", "-size", f"{width}x{max(1, int(width * ratio))}", "xc:white", str(png)],
                "drawing an empty layer")
            return png
        svg = work / f"{tag}-{i}.svg"
        run(["kicad-cli", "pcb", "export", "svg", "--layers", f"{name},Edge.Cuts",
             "--mode-single", "--exclude-drawing-sheet", "--black-and-white",
             "--drill-shape-opt", "2", "-o", str(svg), str(path)], f"exporting {name} ({tag})")
        crop_svg(svg, box)
        run(["magick", "-density", str(density), "-background", "white", str(svg), "-flatten",
             "-resize", f"{width}x{max(1, int(width * ratio))}!", "-colorspace", "gray",
             "-threshold", "50%", "+repage", str(png)], f"rasterising {name} ({tag})")
        return png

    def compare(index_name):
        i, name = index_name
        a, b = raster(i, "old", name), raster(i, "new", name)
        png = work / f"diff-{i}.png"
        # A pale halo around what changed: a removed 2 mm track is otherwise a few red
        # pixels on a sheet of grey.
        radius = max(4, width // 120)
        halos = []
        for tag, expr in (("removed", "u.r<0.5 && v.r>=0.5"), ("added", "u.r>=0.5 && v.r<0.5")):
            halo = work / f"halo-{tag}-{i}.png"
            run(["magick", str(a), str(b), "-fx", f"{expr} ? 1 : 0",
                 "-morphology", "Dilate", f"Disk:{radius}", str(halo)], f"marking {name}")
            halos.append(halo)
        r, g, k, pr, pg = (tuple(int(h[j:j + 2], 16) / 255 for j in (1, 3, 5))
                           for h in (REMOVED, ADDED, KEPT, REMOVED_HALO, ADDED_HALO))

        def pick(c):
            return (f"(wasc&&isc) ? {k[c]:.3f} : (wasc ? {r[c]:.3f} : (isc ? {g[c]:.3f} : "
                    f"(nearr ? {pr[c]:.3f} : (neara ? {pg[c]:.3f} : 1))))")
        run(["magick", str(a), str(b), *map(str, halos), "-colorspace", "sRGB", "-type", "TrueColor",
             # wasc / isc: copper in the old / new version (single letters are taken by -fx).
             "-fx", "wasc=u[0].r<0.5; isc=u[1].r<0.5; nearr=u[2].r>0.5; neara=u[3].r>0.5; "
                    f"channel({pick(0)}, {pick(1)}, {pick(2)})",
             *(["-flop"] if name == "B.Cu" else []), "+repage", str(png)], f"comparing {name}")
        nt, ot = new_tracks.get(name, set()), old_tracks.get(name, set())
        plus, minus = len(nt - ot), len(ot - nt)
        label_ = name if not aliases.get(name) or aliases[name] == name else f"{name} ({aliases[name]})"
        if name == "B.Cu":
            label_ += ", seen from below"
        caption = f"{label_}  ·  " + (f"+{plus} / −{minus} tracks" if plus or minus else "no track changed")
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
  kishot                            every sheet of the only board in this directory
  kishot board.kicad_pcb --fast     quick preview: seconds instead of a minute
  kishot hw/ -s layers -o /tmp      only the copper layers, written to /tmp
  kishot --open --workspace 11      open the images on Hyprland workspace 11
  kishot --diff HEAD~3              what changed in the copper since three commits ago
  kishot -s spin                    an animated GIF of the board turning, for chat

sheets:
  3d      six 3D views
  layers  every copper layer, pours hidden
  diff    every copper layer against an older git revision (--diff REV):
          red removed, green added, grey unchanged
  spin    an animated GIF of the board turning once (one to two minutes; 10 s
          with --fast)
  Without -s: 3d and layers, or only diff when --diff is given.

output:
  <board>-3d.png, <board>-layers.png, <board>-diff.png, <board>-spin.gif,
  in --out-dir; without it, in a
  fabrication/ or fab/ directory next to the board if there is one, else next
  to the board. Each path written is printed on stdout, one per line.

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
                    help="sheet to make, repeatable: 3d, layers, diff, spin, all (default: 3d and "
                         "layers, or diff alone with --diff)")
    ap.add_argument("-d", "--diff", metavar="REV",
                    help="compare the copper with the board at this git revision (a commit, "
                         "tag, branch, HEAD~1...)")
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


def main(argv=None):
    a = parse_args(argv)
    if a.sheet and "diff" in a.sheet and not a.diff:
        print("kishot: error: the diff sheet needs --diff REV", file=sys.stderr)
        return 2
    if not a.sheet:
        wanted = {"diff"} if a.diff else {"3d", "layers"}
    elif "all" in a.sheet:
        wanted = {"3d", "layers", "spin"} | ({"diff"} if a.diff else set())
    else:
        wanted = set(a.sheet)
    sheets = [s for s in SHEETS if s in wanted]
    try:
        pcb = find_board(a.board)
        for tool in ("kicad-cli", "magick"):
            if not shutil.which(tool):
                raise Failure(f"{tool} not found in PATH")
        out_dir = (a.out_dir or default_out_dir(pcb)).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        board = read_board(pcb)
        written = []
        with tempfile.TemporaryDirectory(prefix="kishot-") as t:
            for s in sheets:
                work = Path(t) / s
                work.mkdir()
                out = out_dir / f"{pcb.stem}-{s}.{'gif' if s == 'spin' else 'png'}"
                say(f"{s} sheet of {pcb.name}…")
                if s == "diff":
                    written.append(make_diff(pcb, board, out, a.fast, work, a.diff))
                elif s == "spin":
                    written.append(make_spin(pcb, board, out, a.fast, work))
                else:
                    make = make_3d if s == "3d" else make_layers
                    written.append(make(pcb, board, out, a.fast, work))
                print(out, flush=True)
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
