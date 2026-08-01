# Restore checkout health

Users are receiving 502 responses from checkout. The incident began recently,
and there were no known deployments.

Investigate the running services and restore a successful response from:

```text
http://nginx/health
```

You are working from an incident workstation. Use the Docker CLI to inspect and
repair the sibling service containers. Do not replace the nginx service or
bypass it with a new server; repair the deployed service.
