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
    nginx
    python312
    ripgrep
    vim
  ];

  # Claux release artifacts target conventional glibc-based Linux systems.
  programs.nix-ld.enable = true;

  systemd.services.incident-provision = {
    description = "Provision the Replaybook host-native incident";
    wantedBy = [ "multi-user.target" ];
    before = [ "checkout-backend.service" "incident-nginx.service" ];
    serviceConfig.Type = "oneshot";
    script = ''
      install -d -m 0755 /etc/replaybook /var/lib/replaybook/www /var/log/nginx
      printf 'ok\n' > /var/lib/replaybook/www/health
      if [[ ! -e /etc/replaybook/nginx.conf ]]; then
        cat > /etc/replaybook/nginx.conf <<'EOF'
      pid /run/incident-nginx/nginx.pid;
      events {}
      http {
        access_log /var/log/nginx-access.log;
        error_log stderr notice;

        server {
          listen 80;
          location /health {
            proxy_pass http://127.0.0.1:3001/health;
          }
        }
      }
      EOF
      fi
    '';
  };

  systemd.services.checkout-backend = {
    description = "Replaybook checkout backend";
    wantedBy = [ "multi-user.target" ];
    requires = [ "incident-provision.service" ];
    after = [ "incident-provision.service" "network.target" ];
    serviceConfig = {
      ExecStart = "${pkgs.python312}/bin/python -m http.server 3000 --bind 127.0.0.1 --directory /var/lib/replaybook/www";
      Restart = "always";
      RestartSec = 1;
    };
  };

  systemd.services.incident-nginx = {
    description = "Replaybook incident Nginx";
    wantedBy = [ "multi-user.target" ];
    requires = [ "incident-provision.service" "checkout-backend.service" ];
    after = [ "incident-provision.service" "checkout-backend.service" "network.target" ];
    serviceConfig = {
      RuntimeDirectory = "incident-nginx";
      ExecStartPre = "${pkgs.nginx}/bin/nginx -t -c /etc/replaybook/nginx.conf";
      ExecStart = "${pkgs.nginx}/bin/nginx -c /etc/replaybook/nginx.conf -g 'daemon off;'";
      ExecReload = "${pkgs.nginx}/bin/nginx -s reload -c /etc/replaybook/nginx.conf";
      Restart = "always";
      RestartSec = 1;
    };
  };

  system.stateVersion = "24.05";
}
