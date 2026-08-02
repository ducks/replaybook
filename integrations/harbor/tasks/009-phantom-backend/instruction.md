# Restore backend traffic

The application has failed every backend call since network maintenance. The
backend team reports that its service is healthy, but no requests appear in
its logs.

Investigate the running services from the incident workstation and restore:

    http://app:8080/health

Use the Docker CLI to trace and repair the deployed service topology. Do not
replace the app or backend, and do not bypass the application with another
server. Make the repair survive an app restart.
