{ lib, pkgs, ... }:

let
  appDir = ./app;
  rubyEnv = pkgs.ruby.withPackages (packages: with packages; [ activerecord pg puma rack ]);
in
{
  imports = [ ../worker/base.nix ];

  environment.systemPackages = with pkgs; [ nginx postgresql_16 rubyEnv ];

  services.postgresql = {
    enable = true;
    package = pkgs.postgresql_16;
    ensureDatabases = [ "replaybook" ];
    ensureUsers = [{ name = "replaybook"; ensureDBOwnership = true; }];
    authentication = lib.mkOverride 10 ''
      local all all trust
      host all all 127.0.0.1/32 trust
      host all all ::1/128 trust
    '';
  };

  services.nginx = {
    enable = true;
    virtualHosts.default = { default = true; locations."/".proxyPass = "http://127.0.0.1:3000"; };
  };

  systemd.services.incident-provision = {
    description = "Provision the Replaybook Rails connection-pool incident";
    wantedBy = [ "multi-user.target" ];
    after = [ "postgresql.service" ];
    requires = [ "postgresql.service" ];
    before = [ "checkout-web.service" ];
    serviceConfig.Type = "oneshot";
    path = [ pkgs.coreutils pkgs.postgresql_16 ];
    script = ''
      install -d -m 0755 /etc/replaybook
      if [[ ! -e /etc/replaybook/rails.env ]]; then
        printf '%s\n' 'DB_POOL=1' 'RAILS_MAX_THREADS=4' > /etc/replaybook/rails.env
      fi
      psql --host 127.0.0.1 --username replaybook --dbname replaybook <<'SQL'
      CREATE TABLE IF NOT EXISTS completed_checkouts (
        checkout_id text PRIMARY KEY,
        completed_at timestamptz NOT NULL DEFAULT now()
      );
      SQL
    '';
  };

  systemd.services.checkout-web = {
    description = "Replaybook Rails checkout service";
    wantedBy = [ "multi-user.target" ];
    requires = [ "incident-provision.service" ];
    after = [ "incident-provision.service" "network.target" ];
    serviceConfig = {
      EnvironmentFile = "/etc/replaybook/rails.env";
      WorkingDirectory = "${appDir}";
      ExecStart = "${rubyEnv}/bin/puma --bind tcp://127.0.0.1:3000 --threads 4:4 ${appDir}/app.ru";
      Restart = "always";
      RestartSec = 1;
    };
  };
}
