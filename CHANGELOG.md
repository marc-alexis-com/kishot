# Changelog

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
