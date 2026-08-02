# Restore application health

The application started returning 500 responses about an hour ago. Its logs
show write failures, and there were no known deployments.

Investigate the running services from the incident workstation and restore:

    http://app:8080/health

Use the Docker CLI to inspect and repair the sibling app container. Do not
replace the app or bypass it with another server. Repair the deployed service
and make the recovery survive an app restart.
