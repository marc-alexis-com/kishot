# Publishing kishot

Three channels, each with a recipe here or in `.github/`. What they need from a person:

| Channel | Recipe | Once | At each release |
|---|---|---|---|
| PyPI | `.github/workflows/release.yml` | on pypi.org, add a *pending trusted publisher*: project `kishot`, owner `marc-alexis-com`, repository `kishot`, workflow `release.yml`, environment `pypi`; create the `pypi` environment in the repository's settings | push a tag `vX.Y.Z` matching `__version__` |
| AUR | `aur/PKGBUILD` | an AUR account with an SSH key; `git clone ssh://aur@aur.archlinux.org/kishot.git` | copy the PKGBUILD, set `pkgver`, `updpkgsums`, `makepkg --printsrcinfo > .SRCINFO`, commit, push |
| nixpkgs | `nixpkgs/package.nix` | add yourself to `maintainers/maintainer-list.nix`; the file goes to `pkgs/by-name/ki/kishot/package.nix` | bump `version`, replace `hash` with the one `nix build` prints, open a pull request (or let r-ryantm do it) |

The nixpkgs recipe was built here against this checkout (`src` overridden): it builds, its
tests pass, `kishot --version` runs. The PKGBUILD has not been built: there is no Arch here.

Cost: three places to keep in step with each version, and the AUR and nixpkgs ones are
public promises to keep the package working.
