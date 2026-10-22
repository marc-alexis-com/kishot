"""Builds the README's tour of kishot: every feature on one demo board.

    nix develop -c python3 docs/images/showcase.py docs/images [--demo DIR] [--quality] [--keep DIR]

Needs kicad-cli, ImageMagick, git, numpy and Pillow (the flake's dev shell has them all).
It copies KiCad's RoyalBlue54L Feather demo project to a scratch git repository, commits it,
then edits a copy the way a review round would (tracks removed on F.Cu, a switch moved, a
capacitor's value changed in the schematic) and commits that too. kishot then makes every
sheet against the first commit, and this script assembles them:

  tour.gif       a slideshow: one slide per feature, the command on top, its output below;
                 animated outputs (spin, fade) play inside their slide
  features.png   all the features at a glance, each captioned with its command

--demo points at the RoyalBlue54L Feather project directory when it cannot be found next to
kicad-cli (share/kicad/demos/royalblue54L_feather).
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageSequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
import kishot  # noqa: E402

BOARD = "RoyalBlue54L-Feather"
EDITOR, PAPER, INK, GREY = "#001023", "#f3f4f6", "#111827", "#6b7280"
PROMPT, COMMAND = "#7fc87f", "#e5e7eb"
W, H, BAR = 960, 600, 92  # slide size, command bar height


def font(pattern, size):
    path = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True).stdout
    return ImageFont.truetype(path, size)


MONO = font("DejaVu Sans Mono", 21)
SANS = font("DejaVu Sans", 18)
SANS_SMALL = font("DejaVu Sans", 15)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=demo", "-c", "user.email=demo@example.com",
                    *args], check=True, capture_output=True)


def find_demo(arg):
    if arg:
        return Path(arg)
    cli = shutil.which("kicad-cli")
    candidates = []
    if cli:
        prefix = Path(os.path.realpath(cli)).parent.parent
        candidates.append(prefix / "share/kicad/demos/royalblue54L_feather")
    candidates += [Path(p) for p in glob.glob("/nix/store/*kicad-base*/share/kicad/demos/royalblue54L_feather")]
    candidates.append(Path("/usr/share/kicad/demos/royalblue54L_feather"))
    for c in candidates:
        if (c / f"{BOARD}.kicad_pcb").exists():
            return c
    sys.exit("RoyalBlue54L Feather demo not found: pass --demo DIR")


def review_round(repo):
    """The edits of the example: what a review between two commits could look like."""
    pcb = repo / f"{BOARD}.kicad_pcb"
    s = pcb.read_text(encoding="utf-8")
    items = kishot.top_level_items(s)
    front = [t for h, t in items if h == "segment" and '(layer "F.Cu")' in t]
    for t in front[200:209]:
        s = s.replace(t, "", 1)
    switch = next(t for h, t in items if h == "footprint" and '"Reference" "SW1"' in t)
    at = re.search(r"\(at ([\d.]+) ([\d.]+)", switch)
    s = s.replace(switch, switch.replace(at.group(0), f"(at {float(at.group(1)) + 4:.2f} {at.group(2)}", 1), 1)
    pcb.write_text(s, encoding="utf-8")
    sheet = repo / "sch/nRF54L15.kicad_sch"
    x = sheet.read_text(encoding="utf-8")
    sheet.write_text(x.replace('(property "Value" "300pF"', '(property "Value" "47k"', 1), encoding="utf-8")


def run_kishot(demo, work, quality):
    repo = work / "board"
    shutil.copytree(demo, repo)
    for p in [repo, *repo.rglob("*")]:  # copied from a read-only store
        p.chmod(p.stat().st_mode | 0o200)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "rev A")
    review_round(repo)
    git(repo, "commit", "-qam", "rev B")
    out = work / "out"
    cmd = [sys.executable, str(ROOT / "kishot.py"), str(repo), "-s", "all", "--diff", "HEAD~1",
           "--net", "VBUS", "--drc", "--note", "rev B, for review", "-o", str(out)]
    if not quality:
        cmd.append("--fast")
    print("running", " ".join(cmd[2:]), file=sys.stderr)
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return out


# --- slides ---------------------------------------------------------------------------------

def checkerboard(size, cell=16):
    img = Image.new("RGB", size, "#2b2f36")
    d = ImageDraw.Draw(img)
    for y in range(0, size[1], cell):
        for x in range((y // cell) % 2 * cell, size[0], cell * 2):
            d.rectangle([x, y, x + cell - 1, y + cell - 1], fill="#353a42")
    return img


def frames_of(path):
    """RGBA frames and their durations (ms) of a picture or an animated GIF."""
    img = Image.open(path)
    out = []
    for f in ImageSequence.Iterator(img):
        out.append((f.convert("RGBA"), f.info.get("duration", 100)))
    return out


def fit(img, box, crop=None):
    if crop:
        w, h = img.size
        img = img.crop((int(crop[0] * w), int(crop[1] * h), int(crop[2] * w), int(crop[3] * h)))
    k = min(box[0] / img.width, box[1] / img.height)
    return img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)


def slide_base(command, caption):
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, BAR], fill=EDITOR)
    d.text((24, 18), "$", font=MONO, fill=PROMPT)
    d.text((48, 18), command, font=MONO, fill=COMMAND)
    d.text((24, 56), caption, font=SANS, fill="#9aa7b8")
    return img


AREA = (W - 32, H - BAR - 24)  # where a slide shows its picture
HIGHLIGHT = "#f59e0b"


def hexrgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.int16)


def best_window(mask, frac, aspect):
    """The window `frac` of the picture's width, at `aspect`, holding the most of `mask`,
    then centred on what it holds. None when the mask is empty."""
    if not mask.any():
        return None
    h, w = mask.shape
    s = max(1, w // 400)
    m = mask[:h // s * s, :w // s * s].reshape(h // s, s, w // s, s).any(axis=(1, 3))
    mh, mw = m.shape
    ww = max(4, int(mw * frac))
    wh = max(4, int(ww / aspect))
    if wh > mh:
        wh, ww = mh, int(mh * aspect)
    ii = np.zeros((mh + 1, mw + 1), np.int64)
    ii[1:, 1:] = m.cumsum(0).cumsum(1)
    sums = ii[wh:, ww:] - ii[:-wh, ww:] - ii[wh:, :-ww] + ii[:-wh, :-ww]
    y, x = np.unravel_index(np.argmax(sums), sums.shape)
    ys, xs = np.nonzero(m[y:y + wh, x:x + ww])
    cx, cy = x + xs.mean(), y + ys.mean()
    x0 = int(min(max(cx - ww / 2, 0), mw - ww))
    y0 = int(min(max(cy - wh / 2, 0), mh - wh))
    return x0 * s, y0 * s, (x0 + ww) * s, (y0 + wh) * s


def colour_mask(img, colours, tol=45):
    a = np.asarray(img.convert("RGB"), np.int16)
    mask = np.zeros(a.shape[:2], bool)
    for c in colours:
        mask |= np.abs(a - hexrgb(c)).sum(-1) < tol
    return mask


def motion_mask(a, b, tol=60):
    x = np.asarray(a.convert("RGB"), np.int16)
    y = np.asarray(b.convert("RGB"), np.int16)
    return np.abs(x - y).sum(-1) > tol


def pad_to(img, aspect, colour=PAPER):
    """The picture centred on a canvas of the slide's aspect, and where it went."""
    w, h = img.size
    if w / h < aspect:
        cw, ch = int(h * aspect), h
    else:
        cw, ch = w, int(w / aspect)
    canvas = Image.new("RGBA", (cw, ch), colour)
    ox, oy = (cw - w) // 2, (ch - h) // 2
    canvas.paste(img, (ox, oy), img)
    return canvas, (ox, oy)


def view(canvas, box, target=None, base=None, width=4):
    """The slide frame showing `box` of the canvas, `target` outlined."""
    pic = canvas.crop(tuple(int(v) for v in box)).resize(AREA, Image.LANCZOS)
    frame = base.copy()
    frame.paste(pic, (16, BAR + 12), pic)
    if target:
        k = AREA[0] / (box[2] - box[0])
        x0, y0 = 16 + (target[0] - box[0]) * k, BAR + 12 + (target[1] - box[1]) * k
        x1, y1 = 16 + (target[2] - box[0]) * k, BAR + 12 + (target[3] - box[1]) * k
        ImageDraw.Draw(frame).rectangle([max(14, x0), max(BAR + 10, y0), min(W - 14, x1), min(H - 10, y1)],
                                         outline=HIGHLIGHT, width=width)
    return frame


def ease(t):
    return t * t * (3 - 2 * t)


def focus_of(spec, frames):
    """Where the eye should go on this output, in its own pixels; None for the whole of it."""
    focus = spec.get("focus")
    if not focus:
        return None
    aspect = AREA[0] / AREA[1]
    if focus == "motion":
        mask = motion_mask(frames[0][0], frames[len(frames) // 2][0])
        return best_window(mask, spec.get("frac", 0.4), aspect)
    return best_window(colour_mask(frames[0][0], focus), spec.get("frac", 0.25), aspect)


def slide(spec, out):
    """The frames of one slide: (RGB image, duration ms). A slide with a focus shows the whole
    output with the focus outlined, then zooms in on it and stays there."""
    base = slide_base(spec["command"], spec["caption"])
    frames = frames_of(out / spec["file"])
    target = focus_of(spec, frames)
    aspect = AREA[0] / AREA[1]
    backdrop = spec.get("backdrop")
    result = []
    if len(frames) > 1:  # an animation: played in place, on its focus when it has one
        step = max(1, len(frames) // 36)  # long animations are thinned for the tour
        for img, ms in frames[::step]:
            if target:
                img = img.crop(target)
            canvas, _ = pad_to(img, aspect, "#00000000" if backdrop == "checker" else PAPER)
            if backdrop == "checker":
                board = checkerboard(canvas.size).convert("RGBA")
                board.alpha_composite(canvas)
                canvas = board
            result.append((view(canvas, (0, 0) + canvas.size, base=base), ms * step))
        return result
    canvas, (ox, oy) = pad_to(frames[0][0], aspect)
    whole = (0, 0) + canvas.size
    if not target:
        return [(view(canvas, whole, base=base), spec.get("hold", 2600))]
    t = (target[0] + ox, target[1] + oy, target[2] + ox, target[3] + oy)
    result.append((view(canvas, whole, t, base), 1400))
    steps = 7
    for i in range(1, steps + 1):
        k = ease(i / steps)
        box = tuple(a + (b - a) * k for a, b in zip(whole, t))
        result.append((view(canvas, box, t, base), 60))
    result[-1] = (result[-1][0], spec.get("hold", 2600))
    return result


def slides(stem):
    """Every slide: the command, a caption, the output file, and where to look (colours to find,
    "motion" for what moves in an animation, or nothing for the whole picture)."""
    removed_added = ["#d0342c", "#2e9e4a"]
    return [
        dict(command="kishot board.kicad_pcb", file=f"{stem}-3d.png",
             caption="six 3D views, in the PCB editor's colours, under the title block"),
        dict(command="kishot board.kicad_pcb", file=f"{stem}-layers.png",
             caption="every copper layer, pours hidden: tracks, lengths, plane nets"),
        dict(command="kishot --drc", file=f"{stem}-layers.png", focus=["#d000d0"], frac=0.2,
             caption="KiCad's DRC: counts in the header, each violation ringed where it is"),
        dict(command="kishot --net VBUS", file=f"{stem}-net-VBUS.png", focus=["#c83434"], frac=0.2,
             caption="one net in colour on every layer, the rest of the copper in grey"),
        dict(command="kishot --diff HEAD~1", file=f"{stem}-diff.png", focus=removed_added, frac=0.16,
             caption="the copper against an older commit: red removed, green added"),
        dict(command="kishot --diff HEAD~1", file=f"{stem}-sch-diff.png", focus=removed_added, frac=0.3,
             caption="the schematic too, with a close-up of every change", hold=3000),
        dict(command="kishot --diff HEAD~1", file=f"{stem}-3d-diff.png", focus=["#d0342c"], frac=0.25,
             caption="3D before and after, what moved ringed in red"),
        dict(command="kishot --diff HEAD~1", file=f"{stem}-fade.gif", focus="motion", frac=0.35,
             caption="and a GIF that fades from one version to the other: the switch moved"),
        dict(command="kishot -s spin", file=f"{stem}-spin.gif",
             caption="the board turning, rendered by KiCad, as a GIF for chat"),
        dict(command="kishot -s spin", file=f"{stem}-spin-clear.gif", backdrop="checker",
             caption="the same turn, transparent, for any background"),
        dict(command="kishot -s spin", file=f"{stem}-spin-ps2.gif",
             caption="and pixelated like a PS2 game"),
    ]


def terminal(img, box, paths, size=21):
    d = ImageDraw.Draw(img)
    d.rectangle(box, fill=EDITOR)
    f = font("DejaVu Sans Mono", size)
    y = box[1] + size
    for p in paths:
        if y + size > box[3] - 8:
            break
        d.text((box[0] + 20, y), p, font=f, fill=COMMAND)
        y += int(size * 1.4)


def stdout_slide(paths):
    img = slide_base("kishot -s all --diff HEAD~1 --net VBUS",
                     "for scripts: stdout is the paths written, one per line, nothing else")
    terminal(img, [24, BAR + 24, W - 24, H - 24], paths)
    return [(img, 3200)]


def save_gif(slides_frames, path):
    """One palette per slide, from its first and last frames together: an animation does not
    flicker, and a close-up keeps colours that were a few pixels in the whole view."""
    out, durations = [], []
    for frames in slides_frames:
        first, last = frames[0][0], frames[-1][0]
        both = Image.new("RGB", (first.width, first.height * 2))
        both.paste(first, (0, 0))
        both.paste(last, (0, first.height))
        palette = both.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        for img, ms in frames:
            out.append(img.quantize(palette=palette, dither=Image.Dither.NONE))
            durations.append(max(20, ms))
    out[0].save(path, save_all=True, append_images=out[1:], duration=durations, loop=0, optimize=True)
    # Most of a slide does not move: keep only what changes from one frame to the next.
    subprocess.run(["magick", str(path), "-layers", "Optimize", str(path)], check=True)
    print(path, f"{path.stat().st_size / 1e6:.1f} MB", file=sys.stderr)


def written_paths(out):
    """The files in the order kishot prints them: sheet by sheet, spin's three in a row."""
    order = {n: i for i, n in enumerate(kishot.SHEETS)}

    def key(name):
        sheet = name[len(BOARD) + 1:].rsplit(".", 1)[0]
        base = "net" if sheet.startswith("net-") else "spin" if sheet.startswith("spin") else sheet
        return order.get(base, 99), sheet != "spin", name  # spin.gif before its variants

    return [f"fab/{n}" for n in sorted((p.name for p in out.iterdir()), key=key)]


def tour(out, dest):
    groups = [slide(spec, out) for spec in slides(BOARD)]
    groups.append(stdout_slide(written_paths(out)))
    save_gif(groups, dest / "tour.gif")


def features(out, dest):
    """All the features at a glance: a grid of captioned thumbnails, each on what it shows."""
    specs = slides(BOARD) + [dict(command="kishot -s all --diff HEAD~1 --net VBUS", backdrop="terminal",
                                  caption="for scripts: stdout is the paths written, one per line")]
    cols, cw, ch, gap = 4, 400, 300, 92
    rows = (len(specs) + cols - 1) // cols
    head = 96
    sheet = Image.new("RGB", (cols * cw + 40, head + rows * (ch + gap) + 10), "white")
    d = ImageDraw.Draw(sheet)
    d.text((24, 24), "kishot", font=font("DejaVu Sans:bold", 40), fill=INK)
    d.text((170, 40), "every sheet, from one demo board and one command each", font=SANS, fill=GREY)
    for i, spec in enumerate(specs):
        x, y = 20 + (i % cols) * cw, head + (i // cols) * (ch + gap)
        d.rectangle([x + 4, y, x + cw - 4, y + ch], fill=PAPER)
        if spec.get("backdrop") == "terminal":
            terminal(sheet, [x + 16, y + 16, x + cw - 16, y + ch - 16], written_paths(out), size=13)
        else:
            frames = frames_of(out / spec["file"])
            # The fade's first frame is the older board, whole; a spin's third is a nice angle.
            img = frames[0 if "fade" in spec["file"] else len(frames) // 3][0]
            target = focus_of(spec, frames)
            if target:
                img = img.crop(target)
            pic = fit(img, (cw - 24, ch - 24))
            px, py = x + (cw - pic.width) // 2, y + (ch - pic.height) // 2
            if spec.get("backdrop") == "checker":
                sheet.paste(checkerboard(pic.size), (px, py))
            sheet.paste(pic, (px, py), pic)
            if target and len(frames) == 1:
                d.text((x + 12, y + 8), "close-up", font=SANS_SMALL, fill=HIGHLIGHT)
        d.rectangle([x + 4, y + ch, x + cw - 4, y + ch + 30], fill=EDITOR)
        size = 16
        while size > 10 and d.textlength("$ " + spec["command"], font=font("DejaVu Sans Mono", size)) > cw - 28:
            size -= 1
        d.text((x + 14, y + ch + 5 + (16 - size) // 2), "$ " + spec["command"],
               font=font("DejaVu Sans Mono", size), fill=COMMAND)
        words, line, lines = spec["caption"].split(), "", []
        for w_ in words:
            if d.textlength(line + " " + w_, font=SANS_SMALL) > cw - 24:
                lines.append(line)
                line = w_
            else:
                line = (line + " " + w_).strip()
        lines.append(line)
        for k, text in enumerate(lines[:2]):
            d.text((x + 10, y + ch + 36 + k * 17), text, font=SANS_SMALL, fill=GREY)
    sheet.save(dest / "features.png", optimize=True)
    print(dest / "features.png", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest", type=Path)
    ap.add_argument("--demo")
    ap.add_argument("--quality", action="store_true", help="kishot without --fast (minutes)")
    ap.add_argument("--keep", type=Path, help="reuse kishot's output from this directory")
    a = ap.parse_args()
    a.dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kishot-showcase-") as t:
        out = a.keep if a.keep and a.keep.exists() else run_kishot(find_demo(a.demo), Path(t), a.quality)
        if a.keep and not a.keep.exists():
            shutil.copytree(out, a.keep)
        tour(out, a.dest)
        features(out, a.dest)


if __name__ == "__main__":
    main()
