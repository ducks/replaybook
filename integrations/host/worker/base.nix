{ lib, pkgs, ... }:

let
  publicKeyFile = builtins.getEnv "REPLAYBOOK_HOST_PUBLIC_KEY_FILE";
  sshPortText = builtins.getEnv "REPLAYBOOK_HOST_SSH_PORT";
  httpPortText = builtins.getEnv "REPLAYBOOK_HOST_HTTP_PORT";
  sshPort = if sshPortText == "" then 22600 else builtins.fromJSON sshPortText;
  httpPort = if httpPortText == "" then 22601 else builtins.fromJSON httpPortText;
in
{
  assertions = [
    {
      assertion = publicKeyFile != "" && builtins.pathExists publicKeyFile;
      message = "REPLAYBOOK_HOST_PUBLIC_KEY_FILE must name an existing SSH public key";
    }
  ];

  networking.hostName = "replaybook-incident-host";
  networking.firewall.allowedTCPPorts = [ 22 80 ];

  # vm-nogui adds an interactive terminal resize helper to every login shell.
  # SSH controller commands are noninteractive, so invoking it only emits noise.
  environment.loginShellInit = lib.mkForce "";

  services.openssh = {
    enable = true;
    settings = {
      KbdInteractiveAuthentication = false;
      PasswordAuthentication = false;
      PermitRootLogin = "prohibit-password";
    };
  };

  users.users.root.openssh.authorizedKeys.keys = [
    (lib.removeSuffix "\n" (builtins.readFile publicKeyFile))
  ];

  virtualisation = {
    cores = 2;
    memorySize = 2048;
    diskSize = 12288;
    forwardPorts = [
      {
        from = "host";
        host.port = sshPort;
        guest.port = 22;
      }
      {
        from = "host";
        host.port = httpPort;
        guest.port = 80;
      }
    ];
  };

  environment.systemPackages = with pkgs; [
    bash
    cacert
    curl
    git
    gnutar
    jq
    ripgrep
    vim
  ];

  # Claux release artifacts target conventional glibc-based Linux systems.
  programs.nix-ld.enable = true;

  system.stateVersion = "24.05";
}
