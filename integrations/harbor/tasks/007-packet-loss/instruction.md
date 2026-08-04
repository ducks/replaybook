# Restore reliable backend traffic

Most application requests to the backend are timing out, while a few succeed
slowly. The incident began after network maintenance, with no application
deployment.

Investigate the running services from the incident workstation and restore
reliable responses from:

    http://app:8080/health

Use the Docker CLI to inspect and repair the deployed services. Do not replace
the app or backend and do not bypass either service. The repair must survive
service restarts.
