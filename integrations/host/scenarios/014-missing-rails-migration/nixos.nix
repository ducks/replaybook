{ lib, pkgs, ... }:

let
  appDir = ./app;
  rubyEnv = pkgs.bundlerEnv {
    name = "replaybook-rails-migration-env";
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
    description = "Provision the Replaybook missing migration incident";
    wantedBy = [ "multi-user.target" ];
    after = [ "postgresql.service" "redis-replaybook.service" ];
    requires = [ "postgresql.service" "redis-replaybook.service" ];
    before = [ "checkout-web.service" "checkout-sidekiq.service" ];
    serviceConfig.Type = "oneshot";
    path = [ pkgs.coreutils pkgs.postgresql_16 ];
    script = ''
      install -d -m 0755 /etc/replaybook
      install -d -m 0755 /var/lib/checkout
      ln -sfn ${appDir} /var/lib/checkout/current
      if [[ ! -e /etc/replaybook/checkout.env ]]; then
        printf '%s\n' 'REDIS_URL=redis://127.0.0.1:6379/0' > /etc/replaybook/checkout.env
      fi
      psql --host 127.0.0.1 --username replaybook --dbname replaybook <<'SQL'
      CREATE TABLE IF NOT EXISTS schema_migrations (
        version text PRIMARY KEY
      );
      CREATE TABLE IF NOT EXISTS checkout_confirmations (
        job_id text PRIMARY KEY,
        confirmation_code text NOT NULL,
        completed_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE IF NOT EXISTS job_attempts (
        job_id text PRIMARY KEY,
        attempt_count integer NOT NULL DEFAULT 0
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
      EnvironmentFile = "/etc/replaybook/checkout.env";
      ExecStart = "${rubyEnv.wrappedRuby}/bin/ruby /var/lib/checkout/current/server.rb";
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
      EnvironmentFile = "/etc/replaybook/checkout.env";
      ExecStart = "${rubyEnv}/bin/sidekiq -r /var/lib/checkout/current/jobs.rb -c 2";
      Restart = "always";
      RestartSec = 1;
    };
  };
}
