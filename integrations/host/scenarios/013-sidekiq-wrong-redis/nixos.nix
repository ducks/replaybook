{ lib, pkgs, ... }:

let
  appDir = ./app;
  rubyEnv = pkgs.bundlerEnv {
    name = "replaybook-sidekiq-env";
    gemdir = appDir;
  };
in
{
  imports = [ ../worker/base.nix ];

  environment.systemPackages = with pkgs; [
    nginx
    postgresql_16
    redis
    rubyEnv.wrappedRuby
  ];

  services.postgresql = {
    enable = true;
    package = pkgs.postgresql_16;
    ensureDatabases = [ "replaybook" ];
    ensureUsers = [
      {
        name = "replaybook";
        ensureDBOwnership = true;
      }
    ];
    authentication = lib.mkOverride 10 ''
      local all all trust
      host all all 127.0.0.1/32 trust
      host all all ::1/128 trust
    '';
  };

  services.redis.servers.replaybook = {
    enable = true;
    bind = "127.0.0.1";
    port = 6379;
  };

  services.nginx = {
    enable = true;
    virtualHosts.default = {
      default = true;
      locations."/".proxyPass = "http://127.0.0.1:3000";
    };
  };

  systemd.services.incident-provision = {
    description = "Provision the Replaybook Sidekiq incident";
    wantedBy = [ "multi-user.target" ];
    after = [ "postgresql.service" "redis-replaybook.service" ];
    requires = [ "postgresql.service" "redis-replaybook.service" ];
    before = [ "checkout-web.service" "checkout-sidekiq.service" ];
    serviceConfig.Type = "oneshot";
    path = [ pkgs.coreutils pkgs.postgresql_16 ];
    script = ''
      install -d -m 0755 /etc/replaybook
      if [[ ! -e /etc/replaybook/web.env ]]; then
        printf '%s\n' 'REDIS_URL=redis://127.0.0.1:6379/0' > /etc/replaybook/web.env
      fi
      if [[ ! -e /etc/replaybook/sidekiq.env ]]; then
        printf '%s\n' 'REDIS_URL=redis://127.0.0.1:6379/1' > /etc/replaybook/sidekiq.env
      fi
      psql --host 127.0.0.1 --username replaybook --dbname replaybook <<'SQL'
      CREATE TABLE IF NOT EXISTS completed_jobs (
        job_id text PRIMARY KEY,
        completed_at timestamptz NOT NULL DEFAULT now()
      );
      SQL
    '';
  };

  systemd.services.checkout-web = {
    description = "Replaybook Ruby checkout web service";
    wantedBy = [ "multi-user.target" ];
    requires = [ "incident-provision.service" ];
    after = [ "incident-provision.service" "network.target" ];
    serviceConfig = {
      EnvironmentFile = "/etc/replaybook/web.env";
      ExecStart = "${rubyEnv.wrappedRuby}/bin/ruby ${appDir}/server.rb";
      Restart = "always";
      RestartSec = 1;
    };
  };

  systemd.services.checkout-sidekiq = {
    description = "Replaybook checkout Sidekiq worker";
    wantedBy = [ "multi-user.target" ];
    requires = [ "incident-provision.service" ];
    after = [ "incident-provision.service" "network.target" ];
    serviceConfig = {
      EnvironmentFile = "/etc/replaybook/sidekiq.env";
      ExecStart = "${rubyEnv}/bin/sidekiq -r ${appDir}/jobs.rb -c 2";
      Restart = "always";
      RestartSec = 1;
    };
  };
}
