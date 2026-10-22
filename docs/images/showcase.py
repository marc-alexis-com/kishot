"""Builds the README's tour of kishot: every feature on one demo board.

    nix develop -c python3 docs/images/showcase.py docs/images [--demo DIR] [--quality] [--keep DIR]

Needs kicad-cli, ImageMagick, git and Pillow (the flake's dev shell has them all).
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


def slide(spec, out):
    """The frames of one slide: (RGB image, duration ms)."""
    command, caption, name, crop, hold, backdrop = spec
    base = slide_base(command, caption)
    area = (W - 32, H - BAR - 24)
    frames = frames_of(out / name)
    step = max(1, len(frames) // 36)  # long animations are thinned for the tour
    result = []
    for img, ms in frames[::step]:
        pic = fit(img, area, crop)
        frame = base.copy()
        x, y = (W - pic.width) // 2, BAR + 12 + (area[1] - pic.height) // 2
        if backdrop == "checker":
            frame.paste(checkerboard(pic.size), (x, y))
        frame.paste(pic, (x, y), pic)
        result.append((frame, ms * step))
    if len(result) == 1:
        result[0] = (result[0][0], hold)
    return result


def slides(stem):
    """(command, caption, file, crop (fractions) or None, hold ms, backdrop)."""
    return [
        ("kishot board.kicad_pcb", "six 3D views, in the PCB editor's colours, under the title block",
         f"{stem}-3d.png", None, 2600, None),
        ("kishot board.kicad_pcb", "every copper layer, pours hidden: tracks, lengths, plane nets",
         f"{stem}-layers.png", None, 2600, None),
        ("kishot --drc", "KiCad's DRC: counts in the header, each violation ringed where it is",
         f"{stem}-layers.png", (0.0, 0.0, 0.333, 0.37), 2600, None),
        ("kishot --net VBUS", "one net in colour on every layer, the rest of the copper in grey",
         f"{stem}-net-VBUS.png", (0.0, 0.0, 0.333, 0.37), 2600, None),
        ("kishot --diff HEAD~1", "the copper against an older commit: red removed, green added",
         f"{stem}-diff.png", (0.0, 0.0, 0.333, 0.37), 2600, None),
        ("kishot --diff HEAD~1", "the schematic too, with a close-up of every change",
         f"{stem}-sch-diff.png", (0.0, 0.66, 0.5, 1.0), 2800, None),
        ("kishot --diff HEAD~1", "3D before and after, what moved ringed in red",
         f"{stem}-3d-diff.png", (0.0, 0.0, 1.0, 0.56), 2600, None),
        ("kishot --diff HEAD~1", "and a GIF that fades from one version to the other",
         f"{stem}-fade.gif", None, 0, None),
        ("kishot -s spin", "the board turning, rendered by KiCad, as a GIF for chat",
         f"{stem}-spin.gif", None, 0, None),
        ("kishot -s spin", "the same turn, transparent, for any background",
         f"{stem}-spin-clear.gif", None, 0, "checker"),
        ("kishot -s spin", "and pixelated like a PS2 game",
         f"{stem}-spin-ps2.gif", None, 0, None),
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


def save_gif(frames, path):
    """One palette per slide (taken from its first frame), so an animation does not flicker."""
    out, durations = [], []
    palette = None
    for img, ms in frames:
        if getattr(img, "_new_slide", False) or palette is None:
            palette = img.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
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
    frames = []
    for spec in slides(BOARD):
        s = slide(spec, out)
        s[0][0]._new_slide = True
        frames += s
    s = stdout_slide(written_paths(out))
    s[0][0]._new_slide = True
    frames += s
    save_gif(frames, dest / "tour.gif")


def features(out, dest):
    """All the features at a glance: a grid of captioned thumbnails."""
    specs = slides(BOARD)
    specs = specs + [("kishot -s all --diff HEAD~1 --net VBUS", "for scripts: stdout is the paths written, one per line",
                      None, None, 0, "terminal")]
    cols, cw, ch, gap = 4, 400, 300, 92
    rows = (len(specs) + cols - 1) // cols
    head = 96
    sheet = Image.new("RGB", (cols * cw + 40, head + rows * (ch + gap) + 10), "white")
    d = ImageDraw.Draw(sheet)
    d.text((24, 24), "kishot", font=font("DejaVu Sans:bold", 40), fill=INK)
    d.text((170, 40), "every sheet, from one demo board and one command each", font=SANS, fill=GREY)
    for i, (command, caption, name, crop, _, backdrop) in enumerate(specs):
        x, y = 20 + (i % cols) * cw, head + (i // cols) * (ch + gap)
        d.rectangle([x + 4, y, x + cw - 4, y + ch], fill=PAPER)
        if backdrop == "terminal":
            terminal(sheet, [x + 16, y + 16, x + cw - 16, y + ch - 16], written_paths(out), size=13)
        else:
            img = frames_of(out / name)
            # The fade's first frame is the older board, whole; a spin's third is a nice angle.
            pic = fit(img[0 if "fade" in name else len(img) // 3][0], (cw - 24, ch - 24), crop)
            px, py = x + (cw - pic.width) // 2, y + (ch - pic.height) // 2
            if backdrop == "checker":
                sheet.paste(checkerboard(pic.size), (px, py))
            sheet.paste(pic, (px, py), pic)
        d.rectangle([x + 4, y + ch, x + cw - 4, y + ch + 30], fill=EDITOR)
        d.text((x + 14, y + ch + 5), "$ " + command, font=font("DejaVu Sans Mono", 16), fill=COMMAND)
        words, line, lines = caption.split(), "", []
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
