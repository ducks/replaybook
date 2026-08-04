# Restore checkout database access

Checkout is returning errors on every request. PostgreSQL itself is up and
healthy, and the incident began immediately after the nightly batch window.
There were no application deployments.

Investigate the running services from the incident workstation and restore:

    http://app:8080/health

Use the Docker CLI to identify and repair the source of connection pressure.
Do not replace PostgreSQL, increase its connection limit, or bypass the app.
Make the repair survive service restarts.
