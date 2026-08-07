{ pkgs, ... }:

{
  imports = [ ./base.nix ];

  environment.systemPackages = with pkgs; [
    nginx
    python312
  ];

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
}
