"""Renders the animated pictures of the README: a low-poly Raspberry Pi in KiCad's colours.

    nix develop -c python3 docs/images/render.py docs/images

Needs numpy and Pillow (both in the flake's dev shell) and the DejaVu fonts. Writes:

  banner-dark.gif, banner-light.gif   the title, with the Pi turning in a 3D viewport
  layers.gif                          the Pi coming apart into its copper layers

The 3D pictures come from a small software renderer in the manner of a PS2 game: a few
hundred flat-shaded triangles, a depth buffer, textures sampled without filtering, the
colours reduced to 15 bits with an ordered dither, and the result enlarged by whole pixels.
The board is built from boxes, after the proportions of a Raspberry Pi 4 (85 x 56 mm); the
tracks are drawn, not routed. Colours are KiCad's defaults: the 3D viewer's background
gradient and board colours, the PCB editor's layer colours, and its dark canvas or the
schematic editor's paper for the two banners.
"""
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = sys.argv[1] if len(sys.argv) > 1 else "."

# --- colours -------------------------------------------------------------------------------

def rgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], float) / 255


# KiCad 3D viewer
BG_TOP, BG_BOTTOM = rgb("#ccccE6"), rgb("#666680")
MASK, MASK_TRACK = rgb("#1d5a35"), rgb("#3f9a5c")
GOLD = rgb("#d6ac3c")
SILK = rgb("#ecebe4")
FR4 = rgb("#5a4626")
CHIP, PLASTIC, TIN = rgb("#1b1d21"), rgb("#16171a"), rgb("#9aa5ae")
# PCB editor layer colours (default theme)
LAYERS = [("F.Cu", "#c83434"), ("In1.Cu", "#7fc87f"), ("In2.Cu", "#ce7d2c"), ("B.Cu", "#4d7fc4")]
EDITOR_DARK, PAPER = "#001023", "#f5f4ef"

BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], float)


def bayer(h, w):
    return np.tile(BAYER, (h // 4 + 1, w // 4 + 1))[:h, :w]


def dither15(img):
    """Colours down to 5 bits a channel, ordered dither: the PS2's 15-bit frame buffer."""
    b = (bayer(*img.shape[:2]) + 0.5) / 16
    return np.floor(np.clip(img, 0, 1) * 31 + b[..., None]).clip(0, 31) / 31


# --- textures ------------------------------------------------------------------------------

PX = 1.5  # texture pixels per mm: coarse on purpose, as console textures were
TEX_W, TEX_H = round(85 * PX), round(56 * PX)


def lcg(seed):
    state = [seed]

    def rand():
        state[0] = (state[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return (state[0] >> 16) / 0x7FFF
    return rand


def routes(seed, n):
    """Tracks as a router lays them: straight runs, 45-degree bends, a pad at the end."""
    rand = lcg(seed)
    out = []
    for _ in range(n):
        x, y = rand() * TEX_W, rand() * TEX_H
        pts = [(x, y)]
        horizontal = rand() > 0.5
        for _ in range(3 + int(rand() * 3)):
            step = (7 + rand() * 23) * (1 if rand() > 0.5 else -1)
            if rand() > 0.72:
                x += step
                y += step * (1 if rand() > 0.5 else -1)
            elif horizontal:
                x += step
            else:
                y += step
            horizontal = not horizontal
            pts.append((x, y))
        out.append((pts, 2 if rand() > 0.75 else 1))
    return out


HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]  # mm, from the top left corner


def draw_tracks(d, tracks, colour, pad):
    for pts, w in tracks:
        d.line([(round(x), round(y)) for x, y in pts], fill=colour, width=w + 1)
        x, y = pts[-1]
        d.rectangle([round(x) - 1, round(y) - 1, round(x) + 1, round(y) + 1], fill=pad)


def top_texture():
    """The top face as KiCad's 3D viewer shows it: tracks lighter under the mask, gold pads,
    white silkscreen."""
    img = Image.new("RGBA", (TEX_W, TEX_H), tuple(int(c * 255) for c in MASK))
    d = ImageDraw.Draw(img)
    gold = tuple(int(c * 255) for c in GOLD)
    draw_tracks(d, routes(0x6D2B79F5, 30), tuple(int(c * 255) for c in MASK_TRACK), gold)
    for x, y in HOLES:
        x, y = x * PX, y * PX
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=gold)
        d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(40, 40, 46))
    silk = tuple(int(c * 255) for c in SILK)
    d.rectangle([1, 1, TEX_W - 2, TEX_H - 2], outline=silk)
    for x, y, w in [(15, 60, 17), (60, 66, 11), (75, 30, 9), (30, 32, 7), (48, 75, 14)]:
        d.rectangle([x, y, x + w, y + 1], fill=silk)
    return np.asarray(img, float) / 255


def layer_texture(i, colour):
    """One copper layer alone: its tracks in the layer's colour, the rest transparent."""
    img = Image.new("RGBA", (TEX_W, TEX_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = tuple(int(colour[k:k + 2], 16) for k in (1, 3, 5)) + (255,)
    seed = [0x6D2B79F5, 0x1234567, 0x2468ACE, 0x13579BD][i]
    draw_tracks(d, routes(seed, 30 if i in (0, 3) else 20), c, c)
    for x, y in HOLES:
        d.ellipse([x * PX - 4, y * PX - 4, x * PX + 4, y * PX + 4], outline=c, width=2)
    d.rectangle([0, 0, TEX_W - 1, TEX_H - 1], outline=(208, 210, 205, 255))  # Edge.Cuts
    return np.asarray(img, float) / 255


# --- geometry ------------------------------------------------------------------------------

L, W, T = 1.0, 56 / 85, 1.6 / 85  # the board, in board widths


def mm(x, y):
    """Board coordinates in mm from the top left corner, to the scene's x and z."""
    return (x - 42.5) / 85, (y - 28) / 85


class Mesh:
    def __init__(self):
        self.tris, self.attrs = [], []

    def quad(self, a, b, c, d, colour=None, tex=None, uv=None, two_sided=False, part=0, metal=0.0):
        for tri, tuv in (((a, b, c), (0, 1, 2)), ((a, c, d), (0, 2, 3))):
            self.tris.append(tri)
            self.attrs.append(dict(colour=colour, tex=tex, uv=None if uv is None else [uv[k] for k in tuv],
                                   two_sided=two_sided, part=part, metal=metal))

    def box(self, x0, x1, y0, y1, z0, z1, colour, part=0, metal=0.0, top_tex=None, bottom_tex=None):
        p = lambda x, y, z: (x, y, z)
        v = [p(x0, y0, z0), p(x1, y0, z0), p(x1, y1, z0), p(x0, y1, z0),
             p(x0, y0, z1), p(x1, y0, z1), p(x1, y1, z1), p(x0, y1, z1)]
        uv_top = [(0, 0), (1, 0), (1, 1), (0, 1)]
        faces = [((4, 5, 6, 7), None), ((1, 0, 3, 2), None),  # +z, -z
                 ((5, 1, 2, 6), None), ((0, 4, 7, 3), None),  # +x, -x
                 ((7, 6, 2, 3), "top"), ((0, 1, 5, 4), "bottom")]  # +y, -y
        for idx, which in faces:
            q = [v[i] for i in idx]
            if which == "top" and top_tex is not None:
                self.quad(q[3], q[2], q[1], q[0], tex=top_tex, uv=uv_top, part=part)
                continue
            if which == "bottom" and bottom_tex is not None:
                self.quad(*q, tex=bottom_tex, uv=[(0, 1), (1, 1), (1, 0), (0, 0)], part=part)
                continue
            self.quad(*q, colour=colour, part=part, metal=metal)


# Parts: 0 the board itself, 1 what sits on top, 2 what sits underneath, 3+i copper layer i.
def raspberry(textures):
    m = Mesh()
    hx, hz = L / 2, W / 2
    m.box(-hx, hx, -T / 2, T / 2, -hz, hz, FR4, part=0, top_tex=0, bottom_tex=0)
    top = T / 2

    def part_box(x0, y0, x1, y1, h, colour, metal=0.0, part=1, base=None):
        (a, b), (c, d) = mm(x0, y0), mm(x1, y1)
        b0 = top if base is None else base
        m.box(a, c, b0, b0 + h / 85, b, d, colour, part=part, metal=metal)

    # The 40-pin header: a black base and two rows of gold pins.
    part_box(7.1, 0.9, 57.9, 6.0, 2.5, PLASTIC)
    for i in range(20):
        for row in (2.2, 4.7):
            x = 8.4 + i * 2.54
            part_box(x - 0.45, row - 0.45, x + 0.45, row + 0.45, 6.0, GOLD, metal=0.6, base=top + 2.5 / 85)
    # SoC under its metal lid, RAM, and the small chips.
    part_box(22.0, 25.0, 37.0, 40.0, 2.4, TIN, metal=0.8)
    part_box(41.0, 26.0, 51.0, 39.0, 1.2, CHIP)
    part_box(56.0, 30.0, 62.0, 36.0, 1.0, CHIP)
    part_box(10.0, 12.0, 16.0, 18.0, 1.0, CHIP)
    # Two USB stacks and the Ethernet jack, over the right edge.
    part_box(69.5, 2.5, 87.0, 15.6, 16.0, TIN, metal=0.7)
    part_box(69.5, 20.4, 87.0, 33.5, 16.0, TIN, metal=0.7)
    part_box(65.5, 37.8, 87.5, 53.7, 13.5, TIN, metal=0.7)
    # Their openings, a dark plate just proud of the outer face.
    hole = rgb("#2a2c30")
    for y0, y1, z0, z1 in [(4.0, 14.1, 1.5, 6.5), (4.0, 14.1, 9.0, 14.0),
                           (21.9, 32.0, 1.5, 6.5), (21.9, 32.0, 9.0, 14.0), (40.0, 51.5, 1.5, 11.5)]:
        (a, b), (_, d) = mm(87.1, y0), mm(87.1, y1)
        m.box(a, a + 0.6 / 85, top + z0 / 85, top + z1 / 85, b, d, hole, part=1)
    # Along the bottom edge: USB-C, two micro-HDMI, the audio jack.
    part_box(7.2, 49.5, 16.0, 57.2, 3.2, TIN, metal=0.7)
    part_box(22.6, 50.5, 29.4, 57.2, 3.0, TIN, metal=0.7)
    part_box(36.1, 50.5, 42.9, 57.2, 3.0, TIN, metal=0.7)
    part_box(50.5, 45.0, 56.5, 57.6, 6.0, PLASTIC)
    # The display and camera connectors.
    part_box(1.5, 15.0, 5.5, 37.0, 4.5, PLASTIC)
    part_box(44.0, 42.0, 48.5, 54.0, 4.5, PLASTIC)
    # Underneath: the micro-SD socket, over the left edge, and a few chips.
    bottom = -T / 2
    for x0, y0, x1, y1, h, c, metal in [(-2.0, 22.0, 13.0, 34.0, 1.5, TIN, 0.7),
                                        (40.0, 26.0, 52.0, 38.0, 1.0, CHIP, 0.0),
                                        (20.0, 40.0, 30.0, 46.0, 0.8, CHIP, 0.0)]:
        (a, b), (c2, d) = mm(x0, y0), mm(x1, y1)
        m.box(a, c2, bottom - h / 85, bottom, b, d, c, part=2, metal=metal)
    # The copper layers, inside the board: hidden until it comes apart.
    for i in range(len(LAYERS)):
        y = T / 2 - 0.002 - i * (T - 0.004) / 3
        m.quad((-hx, y, hz), (hx, y, hz), (hx, y, -hz), (-hx, y, -hz),
               tex=1 + i, uv=[(0, 1), (1, 1), (1, 0), (0, 0)], two_sided=True, part=3 + i)
    return m


# --- the renderer --------------------------------------------------------------------------

def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


LIGHTS = [(np.array([-0.45, 0.8, 0.55]), 0.62), (np.array([0.5, 0.3, 0.8]), 0.26)]
LIGHTS = [(d / np.linalg.norm(d), k) for d, k in LIGHTS]


def shade(n, metal):
    """Flat shading of one face, from its normal in camera space."""
    k = 0.28 + sum(w * max(0.0, float(n @ d)) for d, w in LIGHTS)
    spec = 0.0
    if metal:
        h = LIGHTS[0][0] + np.array([0, 0, 1.0])
        h /= np.linalg.norm(h)
        spec = 0.5 * metal * max(0.0, float(n @ h)) ** 18
    return k, spec


def background(h, w):
    t = np.linspace(0, 1, h)[:, None, None]
    return np.broadcast_to(BG_TOP * (1 - t) + BG_BOTTOM * t, (h, w, 3)).copy()


def render(mesh, textures, w, h, spin, elev=0.55, dist=2.6, zoom=2.3, offsets=None, dissolve=None,
           lift=0.0):
    """One frame: the mesh turned by `spin` about the vertical axis, seen from `elev` radians
    above. `offsets` moves each part up or down; `dissolve` is the share of a part's pixels
    left out (screen-door transparency, as consoles did it)."""
    offsets = offsets or {}
    dissolve = dissolve or {}
    R = rot_x(elev) @ rot_y(spin)
    img = background(h, w)
    zbuf = np.zeros((h, w))  # 1 / depth; 0 is infinitely far
    door = (bayer(h, w) + 0.5) / 16
    f = zoom * h / 2
    for tri, a in zip(mesh.tris, mesh.attrs):
        part = a["part"]
        if dissolve.get(part, 0) >= 1:
            continue
        p = np.array(tri, float)
        p[:, 1] += offsets.get(part, 0.0)
        c = (R @ p.T).T
        c[:, 1] -= lift
        depth = dist - c[:, 2]
        sx = w / 2 + f * c[:, 0] / depth
        sy = h / 2 - f * c[:, 1] / depth
        area = (sx[1] - sx[0]) * (sy[2] - sy[0]) - (sx[2] - sx[0]) * (sy[1] - sy[0])
        if abs(area) < 1e-9:
            continue
        n = np.cross(c[1] - c[0], c[2] - c[0])
        n /= np.linalg.norm(n) or 1
        # No back-face culling: the boxes are closed and the depth buffer sorts them out.
        if n[2] < 0:
            n = -n
        k, spec = shade(n, a["metal"])
        x0, x1 = max(int(min(sx)), 0), min(int(max(sx)) + 1, w)
        y0, y1 = max(int(min(sy)), 0), min(int(max(sy)) + 1, h)
        if x0 >= x1 or y0 >= y1:
            continue
        X, Y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        e0 = (sx[2] - sx[1]) * (Y - sy[1]) - (sy[2] - sy[1]) * (X - sx[1])
        e1 = (sx[0] - sx[2]) * (Y - sy[2]) - (sy[0] - sy[2]) * (X - sx[2])
        e2 = (sx[1] - sx[0]) * (Y - sy[0]) - (sy[1] - sy[0]) * (X - sx[0])
        l0, l1, l2 = e0 / area, e1 / area, e2 / area
        inside = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
        if not inside.any():
            continue
        inv = l0 / depth[0] + l1 / depth[1] + l2 / depth[2]
        region = zbuf[y0:y1, x0:x1]
        mask = inside & (inv > region)
        if dissolve.get(part, 0) > 0:
            mask &= door[y0:y1, x0:x1] >= dissolve[part]
        if a["tex"] is not None:
            tex = textures[a["tex"]]
            uv = np.array(a["uv"], float)
            u = (l0 * uv[0, 0] / depth[0] + l1 * uv[1, 0] / depth[1] + l2 * uv[2, 0] / depth[2]) / inv
            v = (l0 * uv[0, 1] / depth[0] + l1 * uv[1, 1] / depth[1] + l2 * uv[2, 1] / depth[2]) / inv
            tu = np.clip((u * tex.shape[1]).astype(int), 0, tex.shape[1] - 1)
            tv = np.clip((v * tex.shape[0]).astype(int), 0, tex.shape[0] - 1)
            texel = tex[tv, tu]
            mask &= texel[..., 3] > 0.5
            colour = texel[..., :3] * k + spec
        else:
            colour = np.broadcast_to(a["colour"] * k + spec, inside.shape + (3,))
        region[mask] = inv[mask]
        img[y0:y1, x0:x1][mask] = colour[mask]
    return dither15(img)


def enlarge(img, k):
    return np.repeat(np.repeat(img, k, axis=0), k, axis=1)


def to_image(img):
    return Image.fromarray((np.clip(img, 0, 1) * 255).round().astype(np.uint8), "RGB")


def gizmo(d, x, y, R, size):
    """The axes in the 3D viewer's corner: X red, Y green, Z blue."""
    for axis, colour in ((np.array([1, 0, 0]), (220, 60, 60)), (np.array([0, 1, 0]), (70, 190, 70)),
                         (np.array([0, 0, 1]), (70, 110, 230))):
        v = R @ axis
        d.line([(x, y), (x + v[0] * size, y - v[1] * size)], fill=colour, width=3)


# --- the pictures --------------------------------------------------------------------------

FRAMES, FRAME_MS = 64, 70  # one turn in about 4.5 s


def font(name, size):
    from subprocess import run
    path = run(["fc-match", "-f", "%{file}", name], capture_output=True, text=True).stdout
    return ImageFont.truetype(path, size)


def save_gif(frames, path):
    """One palette for every frame, built from all of them, so nothing flickers."""
    strip = Image.new("RGB", (frames[0].width, frames[0].height * len(frames[::8])))
    for i, fr in enumerate(frames[::8]):
        strip.paste(fr, (0, i * fr.height))
    pal = strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = [fr.quantize(palette=pal, dither=Image.Dither.NONE) for fr in frames]
    out[0].save(path, save_all=True, append_images=out[1:], duration=FRAME_MS, loop=0,
                optimize=False, disposal=1)
    print(path, f"{os.path.getsize(path) / 1e6:.1f} MB")


def banner(mesh, textures, dark):
    BW, BH, K = 1280, 440, 3
    vw, vh = 216, 132  # the viewport, in render pixels
    vx, vy = BW - vw * K - 36, (BH - vh * K) // 2
    bg = EDITOR_DARK if dark else PAPER
    ink = (240, 242, 245) if dark else (28, 28, 30)
    soft = (150, 162, 182) if dark else (110, 108, 100)
    grid = (34, 52, 82) if dark else (205, 202, 192)
    frame = (60, 80, 110) if dark else (190, 186, 175)
    base = Image.new("RGB", (BW, BH), bg)
    d = ImageDraw.Draw(base)
    for gx in range(20, BW, 24):
        for gy in range(20, BH, 24):
            d.point((gx, gy), fill=grid)
    title, sub, mono = font("DejaVu Sans:bold", 96), font("DejaVu Sans", 27), font("DejaVu Sans Mono", 22)
    d.text((64, 78), "kishot", font=title, fill=ink)
    d.text((68, 200), "Presentation sheets of a KiCad board:", font=sub, fill=soft)
    d.text((68, 236), "3D views, and every copper layer.", font=sub, fill=soft)
    x = 68
    for name, colour in LAYERS:
        d.rectangle([x, 300, x + 16, 316], fill=colour)
        d.text((x + 24, 297), name, font=mono, fill=soft)
        x += 34 + d.textlength(name, font=mono)
    d.text((68, 352), "$ kishot board.kicad_pcb", font=mono, fill=ink)
    d.rectangle([vx - 2, vy - 2, vx + vw * K + 1, vy + vh * K + 1], outline=frame, width=2)
    frames = []
    for i in range(FRAMES):
        spin = -math.tau * i / FRAMES + 0.6
        lift = 0.012 * math.sin(math.tau * i / FRAMES)
        view = render(mesh, textures, vw, vh, spin, zoom=4.4, lift=lift, dissolve={3: 1, 4: 1, 5: 1, 6: 1})
        fr = base.copy()
        fr.paste(to_image(enlarge(view, K)), (vx, vy))
        gizmo(ImageDraw.Draw(fr), vx + 34, vy + vh * K - 34, rot_x(0.55) @ rot_y(spin), 22)
        frames.append(fr)
    save_gif(frames, os.path.join(OUT, f"banner-{'dark' if dark else 'light'}.gif"))


def smooth(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def exploded(mesh, textures):
    vw, vh, K = 320, 180, 3
    n = FRAMES * 2
    frames = []
    mono = font("DejaVu Sans Mono", 20)
    for i in range(n):
        t = i / n
        e = smooth((t - 0.1) / 0.2) - smooth((t - 0.62) / 0.2)
        gap = 0.15 * e
        offsets = {1: 2.4 * gap, 2: -2.4 * gap}
        for k in range(len(LAYERS)):
            offsets[3 + k] = (1.5 - k) * gap
        spin = -math.tau * t + 0.6
        view = render(mesh, textures, vw, vh, spin, elev=0.42, zoom=2.9, offsets=offsets,
                      dissolve={0: min(1.0, e * 1.6)})
        fr = to_image(enlarge(view, K))
        d = ImageDraw.Draw(fr)
        gizmo(d, 40, vh * K - 40, rot_x(0.42) @ rot_y(spin), 24)
        y = 24
        for name, colour in LAYERS:
            d.rectangle([24, y + 3, 40, y + 19], fill=colour, outline=(40, 40, 60))
            d.text((50, y), name, font=mono, fill=(30, 30, 46))
            y += 30
        frames.append(fr)
    save_gif(frames, os.path.join(OUT, "layers.gif"))


def main():
    textures = [top_texture()] + [layer_texture(i, c) for i, (_, c) in enumerate(LAYERS)]
    mesh = raspberry(textures)
    print(f"{len(mesh.tris)} triangles")
    which = sys.argv[2:] or ["dark", "light", "layers"]
    if "dark" in which:
        banner(mesh, textures, dark=True)
    if "light" in which:
        banner(mesh, textures, dark=False)
    if "layers" in which:
        exploded(mesh, textures)


if __name__ == "__main__":
    main()
