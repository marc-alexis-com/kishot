{ lib, stdenvNoCC, makeWrapper, python3, imagemagick, fontconfig, ffmpeg-headless, gerbv, kicad
, withKicad ? true }:

# kicad-cli is appended to PATH, not prepended: a KiCad already installed on
# the machine wins, and its own 3D models and colours are used.
stdenvNoCC.mkDerivation {
  pname = "kishot";
  # Matched on its own line: a `.*` over the whole file overflows Nix's regex stack once
  # kishot.py passes about 100 kB, on some builds of Nix.
  version = builtins.head (builtins.match ''__version__ = "([^"]+)"''
    (lib.findFirst (l: lib.hasPrefix "__version__ = " l) ""
      (lib.splitString "\n" (builtins.readFile ./kishot.py))));
  src = lib.cleanSource ./.;
  nativeBuildInputs = [ makeWrapper ];
  installPhase = ''
    install -Dm755 kishot.py $out/libexec/kishot/kishot.py
    makeWrapper ${python3}/bin/python3 $out/bin/kishot \
      --add-flags $out/libexec/kishot/kishot.py \
      --prefix PATH : ${lib.makeBinPath [ imagemagick fontconfig ffmpeg-headless gerbv ]} \
      ${lib.optionalString withKicad "--suffix PATH : ${lib.makeBinPath [ kicad ]}"}
  '';
  meta = {
    description = "Presentation sheets of a KiCad board: 3D views and copper layers";
    homepage = "https://github.com/marc-alexis-com/kishot";
    license = lib.licenses.mit;
    mainProgram = "kishot";
    platforms = lib.platforms.linux;
  };
}
