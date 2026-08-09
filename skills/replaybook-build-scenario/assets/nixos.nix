{ pkgs, ... }:

{
  imports = [ ../worker/base.nix ];

  environment.systemPackages = with pkgs; [ curl ];

  systemd.services.example = {
    description = "Example deployed service";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      ExecStart = "${pkgs.python3}/bin/python -m http.server 3000";
      Restart = "always";
      RestartSec = 1;
    };
  };
}
