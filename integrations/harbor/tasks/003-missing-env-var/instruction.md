# Restore the application

The application is down and keeps crashing on boot. There were no known
deployments. The app container is running a supervisor, but the application
inside it is not serving requests.

Investigate the app container from the incident workstation and restore:

    http://app:8080/health

Use the Docker CLI to inspect the logs and repair the deployed application's
configuration. Do not replace the app with another server or leave a temporary
process running; make the configuration fix survive an app restart.
