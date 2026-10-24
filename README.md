<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/banner-dark.gif">
    <img src="docs/images/banner-light.gif" alt="kishot: presentation sheets of a KiCad board. A low-poly circuit board turns in a 3D viewport, in KiCad's colours" width="100%">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/marc-alexis-com/kishot/actions/workflows/ci.yml"><img src="https://github.com/marc-alexis-com/kishot/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-1f2937?style=flat-square" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/KiCad-9%20%2F%2010-1f2937?style=flat-square" alt="KiCad 9 / 10">
  <img src="https://img.shields.io/badge/python-3.9%2B%2C%20no%20dependencies-1f2937?style=flat-square" alt="Python 3.9+, no dependencies">
</p>

**`kishot`** turns a KiCad board into sheets you can send: one PNG of six 3D views, one PNG
of the tracks on every copper layer, each under a header that says what the board is; and on
demand, what a review or a fab needs: diffs, a visual BOM, the Gerbers as drawn, a 1:1 PDF. One
command, no GUI, the same picture every time. It is a single Python file that drives
`kicad-cli` and ImageMagick.

```sh
kishot my-board.kicad_pcb
```

## Features

| Sheet | What you get |
|---|---|
| **3D** | top, bottom, three three-quarter views and the profile, in the colours of the PCB editor (solder mask included), cut out on a light background |
| **Copper layers** | every copper layer on its own, front to back, with the copper pours hidden so the routing shows; each one captioned with its track count, length and the net of its plane; the header adds the total length, vias, narrowest track and net classes |
| **One net** | `--net GND`: one net in its layer's colour on every layer, the rest of the copper in grey; its tracks, length, vias and pads |
| **Design rules** | `--drc`: KiCad's DRC runs first; errors and warnings in every header, ringed on the copper layers (errors in magenta, warnings in orange) |
| **Changes** | `--diff REV`, against the board at an older git revision. The copper, layer by layer: removed in red, added in green, the rest in grey, each change ringed so a short track still shows, with tracks and vias added and removed. The schematic, page by page, the same way, with a close-up of each change. The 3D views before and after, side by side, with what changed ringed. And a GIF that fades from one to the other, so a moved part blinks |
| **Notes** | `--note "for the review of 23/10"`: a line of your own at the head of every sheet |
| **Turntable** | `-s spin`: the board, rendered by KiCad, turning once on itself at 20 frames a second, as three animated GIFs (on a light background with its name, transparent without text, pixelated like a PS2 game), a transparent WebP with soft edges and its shadow, and an MP4 (two turns, half the size of the one-turn GIF at `--fast`) when ffmpeg is there. `--frames`, `--seconds`, `--tilt`, and `--swing 30` to swing back and forth instead of turning all the way |
| **X-ray** | `-s xray`: every copper layer at once, each in its KiCad colour and half transparent, pours hidden; from above and from below |
| **Silkscreen** | `-s silk`: the silkscreen as the fab prints it, cut where the mask opens, the pads in grey: to proofread before ordering |
| **Assembly** | `-s assembly`: the fabrication layers with their references (values hidden, they would cover them), side by side, to solder from paper |
| **Visual BOM** | `-s bom`: each fitted part numbered on the board in its group's colour, the list grouped by value and footprint underneath; parts not fitted listed apart |
| **Dimensions** | `-s dims`: the top view with the outline dimensioned, and each mounting hole's diameter and position from the top-left corner |
| **Stackup** | `-s stackup`: the layers, thicknesses, materials and εr of the board's physical stackup, dielectrics drawn to scale, the finish |
| **Gerbers** | `-s gerbers`: the Gerbers and drill file KiCad writes, drawn by gerbv rather than by KiCad and stacked into a board: mask in its stackup colour, exposed copper in its finish, silkscreen, holes |
| **Print at 1:1** | `-s print`: PDFs at scale 1 (the back one mirrored) of the fabrication layer with the pads sketched, to lay the real parts on paper and check the footprints |
| **Variants** | `--variant NAME` (KiCad 10): the parts not fitted in that variant are left out of the renders and drawings |
| **Dark theme** | `--theme dark`: every sheet on a dark background |
| **Batches** | several boards in one command, or `-r DIR` for every board under a directory (autosaves and backups left out) |
| **Watch** | `--watch`: the sheets made again each time the board, its schematic or its project is saved; polled every second, so it takes no inotify watch |
| **Configuration** | a `.kishot.toml` next to the board or above it sets the default sheets, output directory, note, theme… |
| **Pull requests** | a GitHub Action that comments a pull request with the diff sheets, and updates its comment at each push |
| **Header** | title, revision, date and company from the title block (the schematic's, when the board's is empty), board size, copper layers, footprints; the file name and KiCad version in the footer |
| **For scripts** | stdout carries the paths written, one per line, and nothing else, or with `--json` one object with the paths and the board's figures; progress and errors go to stderr; the exit status says whether every sheet was made |
| **Opening** | `--open` shows the images once written; under Hyprland, `--workspace N` puts them on another workspace without leaving yours, and replaces an older window of the same image |

## A look at it

<p align="center">
  <img src="docs/images/tour.gif" alt="A slideshow of kishot: for each feature, the command in a dark bar and what it makes: the 3D sheet, the copper layers, DRC rings, one net, the copper and schematic diffs, 3D before and after, the fade, the three spin GIFs, and the paths printed on stdout" width="100%">
  <br><sub>A tour: each command, and what it makes, on one demo board. Made by
  <a href="docs/images/showcase.py">docs/images/showcase.py</a>, from KiCad's own demo project.</sub>
</p>

<p align="center">
  <img src="docs/images/features.png" alt="Every sheet of kishot at a glance, twelve thumbnails each captioned with its command" width="100%">
</p>

<details>
<summary><b>Every sheet in full size</b></summary>

<p align="center">
  <img src="docs/images/spin.gif" alt="kishot's spin GIF: the RoyalBlue54L Feather, rendered by KiCad, turning once on itself, its name underneath" width="49%">
  <img src="docs/images/spin-ps2.gif" alt="The same turn pixelated like a PS2 game, on the background of KiCad's 3D viewer" width="49%">
  <br><sub><code>kishot -s spin</code>: the board turning, as GIFs that Slack, Discord or a README play
  inline. The plain one and the PS2 one (shown here from <code>--fast</code>); a third, transparent and
  without text, goes on any background.</sub>
</p>

<p align="center">
  <img src="docs/images/3d.png" alt="kishot's 3D sheet of the RoyalBlue54L Feather: top, bottom, three three-quarter views and the profile, under a header with title, revision, date, maker, size, copper layers and footprints" width="100%">
  <br><sub>The 3D sheet: six views of the board, in the PCB editor's colours, under its title block.</sub>
</p>

<p align="center">
  <img src="docs/images/layers.png" alt="kishot's copper-layer sheet of the RoyalBlue54L Feather: eight layers in a grid, each captioned with its track count and plane net" width="100%">
  <br><sub>The copper-layer sheet of the same board, eight layers. With the pours shown, most of
  these would be a solid rectangle of copper: here, the planes are named in the caption and the
  tracks are left to see. The bottom layer is mirrored, as seen from below.</sub>
</p>

<p align="center">
  <img src="docs/images/diff.png" alt="kishot's diff sheet: the eight copper layers in grey, removed tracks in red and added ones in green, each ringed with a pale halo, and the count per layer" width="100%">
  <br><sub><code>kishot --diff HEAD~1</code>: what changed in the copper since the previous commit. Two
  vias moved (on every layer they go through), tracks removed on F.Cu and In5.Cu, a new path on B.Cu.</sub>
</p>

### For a review

<p align="center">
  <img src="docs/images/sch-diff.png" alt="kishot's schematic diff: five pages in grey, one marked changed, and a close-up where the value of C13 goes from 300pF in red to 47k in green" width="100%">
  <br><sub><code>kishot --diff HEAD~1</code>, the schematic: every page compared, and a close-up of what
  changed on each page that did (here, the value of C13).</sub>
</p>

<p align="center">
  <img src="docs/images/3d-diff.png" alt="kishot's 3D before and after: top and bottom views of both versions side by side, the moved switch ringed in red on the current one" width="49%">
  <img src="docs/images/fade.gif" alt="A GIF going from the older board to the current one: the switch slides to its new place" width="49%">
  <br><sub>The 3D views before and after, what changed ringed in red, and the <code>fade</code> GIF
  between the two versions: a part that moved blinks.</sub>
</p>

<p align="center">
  <img src="docs/images/net-VBUS.png" alt="kishot's net sheet: the VBUS net in red on F.Cu, the rest of the copper in light grey on every layer" width="100%">
  <br><sub><code>kishot --net VBUS</code>: where one net runs, layer by layer, and how long it is.</sub>
</p>

</details>

### For fabrication

<p align="center">
  <img src="docs/images/bom.png" alt="kishot's visual BOM: every fitted part of the RoyalBlue54L Feather numbered on the top and bottom views, and the list grouped by value and footprint underneath" width="100%">
  <br><sub><code>kishot -s bom</code>: each line of the BOM has a number and a colour, found on the board.</sub>
</p>

<p align="center">
  <img src="docs/images/gerbers.png" alt="kishot's Gerber sheet: the top and bottom of the board as drawn from the Gerber files, blue solder mask, gold exposed copper, white silkscreen, holes" width="100%">
  <br><sub><code>kishot -s gerbers</code>: what the fab receives, drawn by gerbv from the files KiCad writes,
  in the mask colour and finish of the stackup.</sub>
</p>

<details>
<summary><b>X-ray, assembly, silkscreen, dimensions, stackup, dark theme</b></summary>

<p align="center">
  <img src="docs/images/xray.png" alt="kishot's x-ray sheet: the eight copper layers on top of one another in their KiCad colours, half transparent, from above and below, with a legend" width="100%">
  <br><sub><code>kishot -s xray</code>: every layer at once, to follow a signal from one to the next.</sub>
</p>

<p align="center">
  <img src="docs/images/assembly.png" alt="kishot's assembly sheet: the fabrication layers of both sides with their references, pads in grey" width="100%">
  <br><sub><code>kishot -s assembly</code>: the references on the fabrication layers, values hidden.</sub>
</p>

<p align="center">
  <img src="docs/images/silk.png" alt="kishot's silkscreen sheet: the silkscreen of both sides in ink, the pads in grey" width="100%">
  <br><sub><code>kishot -s silk</code>: the silkscreen as printed, cut where the mask opens.</sub>
</p>

<p align="center">
  <img src="docs/images/dims.png" alt="kishot's dimension sheet: the top view with the width and height of the outline, and the four mounting holes with their diameter and position" width="70%">
  <img src="docs/images/stackup.png" alt="kishot's stackup sheet: the layers of the board from top to bottom, with type, thickness, material and dielectric constant" width="28%">
  <br><sub><code>kishot -s dims</code> and <code>-s stackup</code>.</sub>
</p>

<p align="center">
  <img src="docs/images/layers-dark.png" alt="kishot's copper-layer sheet on a dark background" width="100%">
  <br><sub><code>kishot --theme dark</code>.</sub>
</p>

</details>

These sheets were made by `kishot` from the RoyalBlue54L Feather, a demo project that
ships with KiCad (see [Legal](#legal)), with no option.

## Install

Requirements: **KiCad 9 or later** (for `kicad-cli pcb render`; tested on 10.0.6 and 10.0.7;
`--variant` needs KiCad 10), **ImageMagick 7** (`magick`), Python 3.9 or later (3.11 to read
`.kishot.toml`). `fc-match` (fontconfig) is used to find DejaVu Sans when it is there.
Optional: **ffmpeg** for the spin's MP4, **gerbv** for the Gerber sheet, git for `--diff`.

**Nix** (flake; ImageMagick, ffmpeg and gerbv included, and KiCad too if you have none):

```sh
nix run github:marc-alexis-com/kishot -- my-board.kicad_pcb
nix profile install github:marc-alexis-com/kishot
```

**pipx** or **pip**:

```sh
pipx install git+https://github.com/marc-alexis-com/kishot
```

**Just the file**: `kishot.py` has no dependency outside the standard library.

```sh
curl -Lo ~/.local/bin/kishot https://raw.githubusercontent.com/marc-alexis-com/kishot/main/kishot.py
chmod +x ~/.local/bin/kishot
```

## Usage

```sh
kishot                              # the 3D and copper sheets of the only board here
kishot hw/my-board.kicad_pcb        # a given board
kishot hw/ -s layers                # only the copper layers
kishot -s 3d --fast                 # quick 3D preview
kishot -o out/                      # write somewhere else
kishot --open                       # open the images once written
kishot --open --workspace 11        # Hyprland: on workspace 11, in the background
kishot --drc --note "rev B review"  # DRC results and a note on every sheet
kishot --net GND                    # where one net runs
kishot --diff HEAD~1                # copper, schematic, 3D and fade against the last commit
kishot --diff v1.0 -s diff          # only the copper, against tag v1.0
kishot -s spin                      # the board turning: GIFs, WebP, MP4
kishot -s spin --swing 30 --seconds 3   # swinging ±30° instead, in 3 s
kishot -s bom -s assembly -s print  # what you need to solder it
kishot -s gerbers -s stackup -s dims    # what the fab will see
kishot --theme dark                 # on a dark background
kishot --variant lite               # a KiCad 10 variant: its unfitted parts left out
kishot -r projects/ --json          # every board under projects/, a JSON report
kishot --watch --open --workspace 12    # made again at each save, shown on workspace 12
kishot --diff v1.0 --net VBUS -s all    # everything the options allow
```

```text
usage: kishot [-h] [-r]
              [-s {3d,layers,xray,silk,assembly,bom,dims,stackup,gerbers,print,net,diff,sch-diff,3d-diff,fade,spin,all}]
              [-d REV] [-n NAME] [--drc] [--note TEXT] [--variant NAME]
              [--theme {light,dark}] [-o DIR] [-f] [--json] [--watch]
              [--frames N] [--seconds S] [--tilt DEG] [--swing DEG] [--open]
              [--workspace N] [--viewer CMD] [-V]
              [board ...]

Presentation sheets of a KiCad board: 3D views, copper layers, reviews, fabrication.

positional arguments:
  board                 a .kicad_pcb file, or a directory holding exactly one
                        (default: .); several are made one after the other

options:
  -h, --help            show this help message and exit
  -r, --recursive       every board under the directories given
  -s, --sheet {3d,layers,xray,silk,assembly,bom,dims,stackup,gerbers,print,net,diff,sch-diff,3d-diff,fade,spin,all}
                        sheet to make, repeatable (see below)
  -d, --diff REV        compare with the board at this git revision (a commit,
                        tag, branch, HEAD~1...)
  -n, --net NAME        the net to show (exact name, any case, or its last
                        part: VBAT for /power/VBAT)
  --drc                 run KiCad's DRC: errors and warnings in every header,
                        ringed on the copper layers
  --note TEXT           a line of your own at the head of every sheet
  --variant NAME        the board variant to draw (KiCad 10): parts not fitted
                        in it are left out
  --theme {light,dark}  light (default) or dark sheets
  -o, --out-dir DIR     where to write the images (created if missing)
  -f, --fast            basic 3D quality and smaller images, for a preview
  --json                print one JSON object on stdout: the paths written and
                        the board's figures
  --watch               make the sheets again each time the board, its
                        schematic or its project is saved (polled every
                        second, no inotify)
  --open                open the images once written
  --workspace N         with --open under Hyprland: show them with swayimg on
                        workspace N, without switching to it, replacing an
                        older window of the same image
  --viewer CMD          with --open: the viewer command (default: swayimg,
                        else xdg-open)
  -V, --version         show program's version number and exit

spin:
  --frames N            frames of the turn (default 96, 72 with --fast)
  --seconds S           length of the turn (default 4.8)
  --tilt DEG            tilt of the board (default -55)
  --swing DEG           swing ±DEG back and forth instead of turning all the
                        way round

examples:
```

`kishot --help` also lists every sheet and when it is made. The images are
`<board>-<sheet>.png` (`-3d`, `-layers`, `-xray`, `-silk`, `-assembly`, `-bom`, `-dims`,
`-stackup`, `-gerbers`, `-diff`, `-sch-diff`, `-3d-diff`), `<board>-net-<net>.png`,
`<board>-fade.gif`; for the spin `<board>-spin.gif`, `-spin-clear.gif`, `-spin-ps2.gif`,
`-spin-clear.webp` and `-spin.mp4`; for print `<board>-print.pdf`, and `-print-back.pdf` when
there are parts underneath. Without `--out-dir`, they go to a `fabrication/` or `fab/`
directory next to the board if there is one, else next to the board. `KISHOT_WORKSPACE` and
`KISHOT_VIEWER` set the defaults of the last two options.

Timings, on a laptop (Ryzen AI 7 PRO 350, 8 cores) for the 8-layer board above: about 50 s for
both default sheets, 5 s with `--fast`; the spin three to four minutes (96 frames with floor and
shadows, about 2 GB of memory at its peak, 7 to 9 MB a GIF),
30 s with `--fast` (72 frames, about 2 MB a GIF). Nearly all of it is KiCad's ray tracer. The 2D
sheets (x-ray, silkscreen, assembly, BOM, dimensions, Gerbers) take 5 to 10 s each, the stackup
and the PDFs a second or two.

### Configuration

A `.kishot.toml` next to the board, or in a directory above it up to the top of its git
repository, sets defaults for the options. The command line wins over it, and it over the
environment. It needs Python 3.11 or later (`tomllib`); older ones say so and ignore it.

```toml
sheets = ["3d", "layers", "bom"]   # what plain `kishot` makes here
out_dir = "fab"                    # relative to this file
note = "rev B"
theme = "dark"
drc = true
workspace = "12"
swing = 30                         # and frames, seconds, tilt for the spin
```

An unknown key, or a value of the wrong type, is an error rather than ignored.

### Batches and watching

Several boards can be named; `-r` takes every board under a directory, leaving out KiCad's
autosaves, `*-backups` directories and hidden ones. A board that fails does not stop the others;
the exit status is 1 if any did. Two boards with the same name cannot share one `--out-dir`.

`--watch` makes the sheets once, then again each time a watched board, a schematic next to it,
or its project file changes, until Ctrl+C. It checks the files' dates every second rather than
asking inotify, whose quota a file synchroniser can use up entirely. With `--open --workspace N`
the new images replace the old windows; with another viewer, they are opened the first time only.

## For scripts

`kishot` is meant to be called by other programs as much as by people:

- **stdout** is the list of images written, one absolute path per line, printed as each sheet
  is done. Nothing else is ever printed there.
- **`--json`** prints one JSON object instead, once everything is done (one per run with
  `--watch`): `{"kishot": version, "ok": bool, "boards": [...]}`, each board with its path,
  `ok`, `error`, the paths written per sheet, and its figures: title block, size, thickness,
  each copper layer's tracks, length and planes, vias, narrowest track, net classes,
  footprints, parts fitted, DRC counts.
- **stderr** carries progress (`kishot: 3d sheet of board.kicad_pcb…`) and errors, one line
  each, without a traceback.
- **Exit status**: `0` when every sheet asked for was written, `1` on any failure (no board,
  `kicad-cli` missing or failing, ImageMagick missing), `2` on a usage error.
- No prompt, no window unless `--open` is passed.

```sh
kishot -s layers --fast | while read -r png; do upload "$png"; done
kishot -r hw/ --json -s stackup | jq '.boards[].figures | {title, thickness_mm, vias}'
```

## GitHub Action

The repository is also an Action: on a pull request, it draws the diff sheets of a board
against the pull request's base, pushes the images to a branch of their own (a comment can only
show an image that has a URL) and writes one comment, updated in place at each push.

```yaml
on: pull_request
permissions:
  contents: write        # to push the images to the kishot-images branch
  pull-requests: write   # to comment
jobs:
  board:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: marc-alexis-com/kishot@v0.5.0
        with:
          board: hw/                         # default: the repository's top
          sheets: diff sch-diff 3d-diff      # the default
          args: --fast --drc                 # default: --fast
```

It installs Nix and runs kishot from this repository's flake, KiCad included: the first run
downloads KiCad, a few minutes. Pull requests from forks get a read-only token, so the push
and the comment fail there. The JSON report is the step's `report` output.


## How it works

**3D sheet.** Six `kicad-cli pcb render` runs in parallel, with the `follow_pcb_editor` preset:
the default preset follows the plot settings, which hide the solder mask, and the board would
come out as bare copper. Each render is cut out along its alpha channel and centred on a tile.
When a part sticks out of the frame (a cable, a long connector, a tall capacitor), the view is
rendered again further out, up to twice, and a warning says so if it is still cut.

<p align="center">
  <img src="docs/images/layers.gif" alt="A low-poly board comes apart: its parts rise, the board fades, and four copper layers float apart in KiCad's layer colours, red, green, orange and blue, then close up again" width="100%">
  <br><sub>What the copper-layer sheet shows: the board without its body and pours, one layer at a time.</sub>
</p>

**Copper-layer sheet.** On a board with a ground plane, the 3D view shows copper everywhere
under the mask, and the routing cannot be seen. `kishot` writes a copy of the board without the
zones' fill (every `filled_polygon` block; the zone outlines stay), then exports each copper
layer with its board outline to SVG (`kicad-cli pcb export svg --mode-single`), the back layer
mirrored, and rasterises it. Silkscreen and fabrication layers are left out on purpose: their
text hides the tracks. A pour counts as a plane, and is named in the caption, when its outline
covers at least 30 % of the board.

**Spin.** 96 renders (72 with `--fast`), the board tilted and turned a little more each time:
one turn in 4.8 s, 20 frames a second (14 with `--fast`). Every frame is checked against the
edges, as for the 3D sheet, since a part can leave the frame at one angle only. All frames are
cut to the same box, the union of what they show, so the board turns in place instead of being
re-centred at each frame, and each GIF has one palette for all its frames, so the colours do not
flicker. Three GIFs come out of the same renders:

- `-spin.gif`, on a light background, the board's name underneath;
- `-spin-clear.gif`, transparent and without text. GIF transparency is all or nothing, so the
  shadow on the floor is left out and the edges are not smoothed against your background;
- `-spin-ps2.gif`, drawn at a third of the size on the gradient of KiCad's 3D viewer, its colours
  cut to 15 bits with an ordered dither, as the PS2 did, then enlarged by whole pixels.

Two more files come out of the spin: `-spin-clear.webp`, which keeps a real alpha channel (soft
edges, the shadow on the floor), and with ffmpeg `-spin.mp4`, H.264 for any player, two turns
long since a chat app shows a video once before looping it. `--swing DEG` replaces the turn by a
pendulum, `DEG × sin(2π t)`, which ends where it began and so loops without a jump.

**2D sheets.** The x-ray, silkscreen, assembly, BOM and dimension sheets all draw each layer they
need in black and white with `kicad-cli pcb export svg`, framed on the outline in millimetres as
the copper sheet is, then paint each in its colour over the background. Silkscreen, assembly,
BOM and dimensions start from a copy of the board without tracks, vias or zones, so only the
pads are left to find one's way; the assembly copy also hides every footprint's value, which
KiCad writes on the fabrication layer over the reference. Numbers, rings, arrows and labels are
an SVG in the board's own millimetres, drawn over the picture. The BOM groups fitted parts by
value and footprint (`exclude_from_bom` and references starting with `#` left out, `dnp` listed
apart). The dimensions give the outline's bounding box, and the holes of mounting-hole
footprints and unplated holes of 2 mm or more, placed from the top-left corner with KiCad's
y downwards.

**Stackup.** Read from the board's `(setup (stackup …))`, as Board Setup writes it: each layer's
type, thickness, material, εr and colour, and the copper finish. Dielectrics are drawn to scale,
the thin layers one line of text high.

**Gerbers.** `kicad-cli pcb export gerbers` and `export drill` write what a fab would get; gerbv
draws each file in black and white on the same window, in the board's coordinates (Gerber's y
points up). The board is what the outline encloses, found by flooding the outside from a
corner; then, pixel by pixel: a hole or outside the board, background; silkscreen; copper where
the mask opens, in the finish's colour (gold for ENIG); a mask opening without copper, bare FR4;
copper under the mask, a lighter mask; else the mask, in its stackup colour (green by default).

**Print.** `kicad-cli pcb export pdf` at scale 1 on the board's page, the fabrication and
courtyard layers with the pads sketched and the outline, in black and white; the back one
mirrored, as the parts are seen from below.

**Design rules.** `--drc` runs `kicad-cli pcb drc` on the board with the project's own rules,
errors and warnings (exclusions left out). Each violation's first item gives a position, and its
description the layer ("Track on F.Cu", or the layer's user name): it is ringed there, thick and
magenta for an error (no copper layer of KiCad's default theme uses it, so a ring shows even on
red F.Cu), thin and orange for a warning, on every layer when it names none. The counts are
KiCad's: a demo board whose vias are finer than its own drill rule shows hundreds of errors.

**One net.** `--net` takes the exact name, the name in any case, or its last part (`VBAT` for
`/power/VBAT`) when only one net ends that way; otherwise it lists the closest names. Each layer is
drawn twice from two copies of the board, every copper item in grey, then only the net's: its
tracks, vias, pads and zones (filled, since a net's own plane is part of where it runs).

**Diff sheets.** `git archive REV` unpacks the board's project directory as it was, so the older
schematic and any 3D model kept in the project come with it.

The copper: both versions lose their pour fill, each copper layer is exported from both in black and white, and both SVGs are framed on
the same box, the union of the two outlines, in the board's own millimetres: an outline that
grew between the two still lines up. A pixel is grey when it is copper in both, red when only
in the old one, green when only in the new one, and anything that changed gets a pale halo.

The schematic: every page of both versions, hierarchy included, is exported to SVG in black and
white and compared the same way; a page that changed gets a second tile, drawn again from the SVGs
at a higher scale and centred on what changed. The 3D before and after are the top and bottom
renders of both boards, and the places that differ by more than a little are ringed in red on the
current one; `fade` crossfades the two top views.

The counts compare tracks and vias by their geometry (ends, width, size), not by their ids, so
a track that did not move is the same track even when KiCad renumbered it. A layer that exists
in only one version is shown entirely red or green.

The board file is read by a small S-expression scanner (quoted strings included), not by
regular expressions over the whole file, so the formatting of the file does not matter.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `the gerbers sheet needs gerbv` | gerbv is not installed. With `-s all` the sheet is left out instead. |
| `kicad-cli … unknown argument --variant` | Variants arrived in KiCad 10. |
| `.kishot.toml ignored: reading it needs Python 3.11` | `tomllib` is in the standard library from 3.11. |
| `no ffmpeg: no MP4 of the spin` | The GIFs and the WebP are made anyway. |
| The BOM numbers sit on top of one another | A dense board at `--fast`: without it the views are twice as large. |
| A mounting hole is missing on the dimension sheet | Only holes of mounting-hole footprints, and unplated holes of 2 mm or more, are dimensioned; a connector's pegs are not. |
| Printed, the PDF is not to scale | The print dialog shrank it to fit the page: print at 100 % ("actual size"). |
| The board comes out orange (bare copper) in 3D | The solder mask is hidden in the PCB editor's 3D viewer settings: `kishot` uses that preset (`follow_pcb_editor`). |
| A part is missing in 3D (a bare footprint, a hole) | The footprint has no 3D model, or KiCad cannot find it: check `KICAD9_3DMODEL_DIR` / `KICAD10_3DMODEL_DIR`. `kishot` renders what KiCad renders. |
| `view … still touches the frame` | Something very large sticks out of the board. The sheet is still made; that view is cut. |
| `rendering view 1 failed` | The output of `kicad-cli` follows the message. A KiCad older than 9 has no `pcb render`. |
| A plane's tracks still look solid | The zone is not a pour but a thick track or a pad. Only zone fills are hidden. |
| `reading board.kicad_pcb at REV failed` | git's own message follows: an unknown revision, or a board that did not exist yet at that revision (or had another name). |
| The whole diff is red and green, shifted | The board was moved on the page between the two versions; the diff compares page coordinates. |
| Hundreds of DRC errors on a board you trust | They are KiCad's, with the project's rules: `kicad-cli pcb drc` on the board says the same. The usual one is a drill or clearance rule stricter than the board. |
| `no net X on this board; close: …` | Net names keep their sheet path (`/power/VBAT`); the closest names are listed. |
| The 3D before and after ring nothing although a part moved | The two renders must have the same size: a board whose outline changed is framed differently. The copper diff still shows it. |
| `--workspace needs Hyprland, hyprctl and swayimg` | Outside Hyprland, `--workspace` is ignored and the images open the usual way. |

## Development

```sh
nix develop                                   # optional: Python (numpy, Pillow), ImageMagick, fontconfig
python3 -m unittest discover -s tests -v      # parser, layer order, planes, command line
nix flake check                               # the same tests, in the Nix sandbox
python3 docs/images/render.py docs/images     # the banner and the exploded view (numpy, Pillow)
python3 docs/images/showcase.py docs/images   # the tour and the overview, from KiCad's demo (a minute)
```

The tests need neither KiCad nor ImageMagick. The whole tool is `kishot.py`:

| Part | Role |
|---|---|
| reading the board | S-expression scanner, title block, outline, copper layers, tracks, planes, fill stripping |
| drawing | the calls to `kicad-cli` and `magick`, tiles, grid, header and footer |
| opening | the viewer, and the Hyprland path (Lua or classic configuration) |
| command line | arguments, output directory, exit status |

## Contributing

Issues and pull requests are welcome, especially a board that comes out wrong. The most useful
report says the KiCad version (`kicad-cli --version`), the ImageMagick version, and, when the
board is public, a link to it. Please keep pull requests to the standard library.

## Legal

The banner and the exploded view are generated by [docs/images/render.py](docs/images/render.py), a small
software renderer in the manner of a PS2 game: a low-poly board built from boxes after the
proportions of a Raspberry Pi 4, its tracks drawn at random, in KiCad's default colours. It
uses no Raspberry Pi design file; "Raspberry Pi" is a trademark of Raspberry Pi Ltd.

Every other picture shows the **RoyalBlue54L Feather** by Lord's Boards
([project](https://www.crowdsupply.com/lords-boards/royalblue54l-feather)), released under the
CERN Open Hardware Licence v2, Permissive, and shipped with KiCad as a demo project, rendered by
`kishot`. The diff pictures compare it with copies edited for the example: tracks removed, vias
and a switch moved, a path added, and one capacitor's value changed in the schematic. This project is not affiliated with KiCad
or with Lord's Boards; "KiCad" is used here only to name the software it works with.

## License

[MIT](LICENSE)
