# Changelog

## Unreleased

- DRC errors are ringed in magenta, not red: on F.Cu, red rings disappeared in the copper.
- README: a tour (`tour.gif`, one slide per feature, the command above its output) and an
  overview (`features.png`), built by `docs/images/showcase.py` from KiCad's RoyalBlue54L demo
  and a scripted review round; the full-size sheets folded under them.

## 0.4.0 — 2026-10-22

- **`--drc`**: KiCad's DRC first; errors and warnings in every header, ringed on the copper
  layers (red errors, thin orange warnings), at the position and on the layer KiCad gives.
- **`--net NAME`**: a sheet with one net in its layer's colour, the rest of the copper in grey;
  tracks, length, vias and pads. Names match exactly, in any case, or by their last part.
- **`--diff`** now makes four sheets: the copper as before; **`sch-diff`**, every schematic page
  compared, with a close-up of each change; **`3d-diff`**, top and bottom before and after, what
  changed ringed in red; **`fade`**, a GIF from one version to the other. The older project is
  unpacked with `git archive`, so its schematic and its own 3D models come along.
- **`--note TEXT`**: a line of your own at the head of every sheet.
- The copper sheet's captions give each layer's track length; its header the total length, vias,
  narrowest track and net classes.
- The copper sheet is now framed on the outline in millimetres, as the diff is (DRC markers land
  on the right pixel); B.Cu is mirrored after rendering.

## 0.3.0 — 2026-10-22

- **`-s spin`**: the board, rendered by KiCad, turning once on itself, as three animated GIFs
  from the same renders: on a light background with its name, transparent without text
  (`-clear`), and pixelated like a PS2 game (`-ps2`). 96 frames, 20 a second (72 and 14 with
  `--fast`). Frames that touch the edge make the whole turn zoom out; one crop and one palette
  for every frame. Part of `-s all`, not of the default.

## 0.2.0 — 2026-10-22

- **`--diff REV`**: a new sheet, every copper layer compared with the board at an older git
  revision. Removed copper in red, added in green, unchanged in grey, a pale halo around each
  change; tracks and vias added and removed, per layer and in total. Both versions are framed
  on the union of their outlines, so a board whose outline changed still lines up.
- Without `-s`, `--diff` makes the diff sheet alone; `-s all --diff REV` makes all three.

- README: an animated banner, a low-poly board turning in KiCad's 3D viewer colours, and an
  exploded view of its copper layers, drawn by `docs/images/render.py` (PS2-style software
  renderer, numpy and Pillow).

## 0.1.0 — 2026-10-22

First release.

- **3D sheet**: six `kicad-cli pcb render` views (top, bottom, three three-quarter views,
  profile) in the PCB editor's colours, under a header from the title block. A view whose
  render touches the frame is rendered again further out.
- **Copper-layer sheet**: every copper layer with the pours' fill removed, the back layer
  mirrored, each captioned with its track count and plane nets.
- Output made for scripts: image paths on stdout, one per line, everything else on stderr.
- `--open`, with `--workspace N` under Hyprland (swayimg in the background, an older window of
  the same image replaced).
- Nix flake, `pyproject.toml`, tests of the board reader.
