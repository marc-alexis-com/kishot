# Publishing kishot

Three channels, each with a recipe here or in `.github/`. What they need from a person:

| Channel | Recipe | Once | At each release |
|---|---|---|---|
| PyPI | `.github/workflows/release.yml` | on pypi.org, add a *pending trusted publisher*: project `kishot`, owner `marc-alexis-com`, repository `kishot`, workflow `release.yml`, environment `pypi` (the environment exists in the repository since 2026-10-24) | push a tag `vX.Y.Z` matching `__version__` |
| AUR | `aur/PKGBUILD` | an AUR account with an SSH key; `git clone ssh://aur@aur.archlinux.org/kishot.git` | copy the PKGBUILD, set `pkgver`, `updpkgsums`, `makepkg --printsrcinfo > .SRCINFO`, commit, push |
| nixpkgs | `nixpkgs/package.nix` | add yourself to `maintainers/maintainer-list.nix`; the file goes to `pkgs/by-name/ki/kishot/package.nix` | bump `version`, replace `hash` with the one `nix build` prints, open a pull request (or let r-ryantm do it) |

Tested on 2026-10-24: the nixpkgs recipe builds against this checkout (`src` overridden), its
tests pass and `kishot --version` runs; the PKGBUILD builds in an `archlinux` container from a
local tarball (`makepkg -d`: KiCad itself not installed), its tests pass, and namcap only warns
about dependencies absent from the container. The PyPI workflow has not run: it needs the
trusted publisher first, and a tag.

Cost: three places to keep in step with each version, and the AUR and nixpkgs ones are
public promises to keep the package working.
