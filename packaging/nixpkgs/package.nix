# For nixpkgs: pkgs/by-name/ki/kishot/package.nix. The flake's own package.nix builds from
# this checkout; this one fetches a tag, as nixpkgs wants.
{
  lib,
  python3Packages,
  fetchFromGitHub,
  imagemagick,
  fontconfig,
  ffmpeg-headless,
  gerbv,
  kicad,
}:

python3Packages.buildPythonApplication rec {
  pname = "kishot";
  version = "0.5.0";
  pyproject = true;

  src = fetchFromGitHub {
    owner = "marc-alexis-com";
    repo = "kishot";
    tag = "v${version}";
    hash = lib.fakeHash; # nix build tells the right one once the tag exists
  };

  build-system = [ python3Packages.setuptools ];

  # kicad-cli is appended, not prepended: a KiCad already on the PATH wins, with its own
  # 3D models.
  makeWrapperArgs = [
    "--prefix"
    "PATH"
    ":"
    (lib.makeBinPath [ imagemagick fontconfig ffmpeg-headless gerbv ])
    "--suffix"
    "PATH"
    ":"
    (lib.makeBinPath [ kicad ])
  ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  pythonImportsCheck = [ "kishot" ];

  meta = {
    description = "Presentation sheets of a KiCad board: 3D views, copper layers, diffs, BOM, Gerbers";
    homepage = "https://github.com/marc-alexis-com/kishot";
    changelog = "https://github.com/marc-alexis-com/kishot/blob/v${version}/CHANGELOG.md";
    license = lib.licenses.mit;
    maintainers = [ ]; # add yourself to maintainers/maintainer-list.nix first
    mainProgram = "kishot";
    platforms = lib.platforms.linux;
  };
}
