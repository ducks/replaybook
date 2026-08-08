{ lib, pkgs, ... }:

let
  sourceDir = ./app;
  rubyEnv = pkgs.bundlerEnv {
    name = "replaybook-sidekiq-poison-env";
    gemdir = sourceDir;
  };
in
{
  imports = [ ../../worker/base.nix ];

  environment.systemPackages = with pkgs; [ nginx postgresql_16 redis rubyEnv.wrappedRuby ];

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

  services.redis.servers.replaybook = { enable = true; bind = "127.0.0.1"; port = 6379; };
  services.nginx = {
    enable = true;
    virtualHosts.default = { default = true; locations."/".proxyPass = "http://127.0.0.1:3000"; };
  };

  systemd.services.incident-provision = {
    description = "Provision the Replaybook Sidekiq poison-pill incident";
    wantedBy = [ "multi-user.target" ];
    after = [ "postgresql.service" "redis-replaybook.service" ];
    requires = [ "postgresql.service" "redis-replaybook.service" ];
    before = [ "checkout-web.service" "checkout-sidekiq.service" ];
    serviceConfig.Type = "oneshot";
    path = [ pkgs.coreutils pkgs.postgresql_16 ];
    script = ''
      install -d -m 0755 /etc/replaybook /var/lib/checkout/releases
      if [[ ! -e /var/lib/checkout/releases/current ]]; then
        cp -R ${sourceDir} /var/lib/checkout/releases/current
      fi
      ln -sfn /var/lib/checkout/releases/current /var/lib/checkout/current
      if [[ ! -e /etc/replaybook/checkout.env ]]; then
        printf '%s\n' 'REDIS_URL=redis://127.0.0.1:6379/0' > /etc/replaybook/checkout.env
      fi
      psql --host 127.0.0.1 --username replaybook --dbname replaybook <<'SQL'
      CREATE TABLE IF NOT EXISTS completed_jobs (job_id text PRIMARY KEY, completed_at timestamptz NOT NULL DEFAULT now());
      CREATE TABLE IF NOT EXISTS quarantined_jobs (job_id text PRIMARY KEY, reason text NOT NULL, quarantined_at timestamptz NOT NULL DEFAULT now());
      CREATE TABLE IF NOT EXISTS job_attempts (job_id text PRIMARY KEY, attempt_count integer NOT NULL DEFAULT 1);
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
      ExecStart = "${rubyEnv}/bin/sidekiq -r /var/lib/checkout/current/jobs.rb -c 1";
      Restart = "always";
      RestartSec = 1;
    };
  };
}
