{ ... }:

{
  imports = [
    (builtins.toPath (builtins.getEnv "REPLAYBOOK_HOST_SCENARIO_CONFIG"))
  ];

  # The default NixOS VM runner exposes the host's complete Nix store over 9p.
  # A dedicated image limits the guest to the selected scenario's closure.
  virtualisation.useNixStoreImage = true;
  virtualisation.mountHostNixStore = false;
  virtualisation.writableStore = true;
}
