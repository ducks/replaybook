# Restore the flapping application

The application process comes up briefly and then dies repeatedly. There were
no deployments or code changes before the incident.

Investigate the running services from the incident workstation and restore:

    http://app:8080/health

Use the Docker CLI to inspect and repair the deployed app container. Do not
replace the container, raise its memory limit, or bypass it with another
server. Make the repair survive an app restart.
