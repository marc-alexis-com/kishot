{
  description = "Presentation sheets of a KiCad board: 3D views and copper layers";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        kishot = pkgs.callPackage ./package.nix { };
        default = kishot;
      });

      checks = forAll (pkgs: {
        tests = pkgs.runCommand "kishot-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
          cp -r ${self}/kishot.py ${self}/tests .
          python3 -m unittest discover -s tests
          touch $out
        '';
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.imagemagick pkgs.fontconfig ];
        };
      });
    };
}
