{ lib, pkgs, ... }:

let
  publicKeyFile = builtins.getEnv "REPLAYBOOK_WORKER_PUBLIC_KEY_FILE";
  sshPortText = builtins.getEnv "REPLAYBOOK_WORKER_SSH_PORT";
  sshPort = if sshPortText == "" then 22222 else builtins.fromJSON sshPortText;
in
{
  assertions = [
    {
      assertion = publicKeyFile != "" && builtins.pathExists publicKeyFile;
      message = "REPLAYBOOK_WORKER_PUBLIC_KEY_FILE must name an existing SSH public key";
    }
  ];

  networking.hostName = "replaybook-eval-worker";
  networking.firewall.allowedTCPPorts = [ 22 ];

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
    memorySize = 4096;
    diskSize = 24576;
    forwardPorts = [
      {
        from = "host";
        host.port = sshPort;
        guest.port = 22;
      }
    ];
    docker.enable = true;
  };

  environment.systemPackages = with pkgs; [
    bash
    cacert
    curl
    docker
    docker-buildx
    docker-compose
    git
    gnutar
    jq
    python312
    uv
  ];

  system.stateVersion = "24.05";
}
