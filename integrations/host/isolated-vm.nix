{ lib, pkgs, ... }:

let
  clauxBinary = builtins.getEnv "REPLAYBOOK_HOST_CLAUX_BINARY";
  clauxPackage = pkgs.runCommand "replaybook-claux" { } ''
    install -Dm755 ${builtins.path { path = clauxBinary; name = "claux-binary"; }} \
      $out/bin/claux
  '';
in

{
  imports = [
    (builtins.toPath (builtins.getEnv "REPLAYBOOK_HOST_SCENARIO_CONFIG"))
  ];

  # The default NixOS VM runner exposes the host's complete Nix store over 9p.
  # A dedicated image limits the guest to the selected scenario's closure.
  virtualisation.useNixStoreImage = true;
  virtualisation.mountHostNixStore = false;
  virtualisation.writableStore = true;

  # The built-in adapter's pinned executable is part of the guest closure.
  # Custom adapters continue staging their optional payloads at runtime.
  environment.systemPackages = lib.optionals (clauxBinary != "") [ clauxPackage ];
}
