# Restore background job processing

Emails have stopped sending and password-reset jobs are piling up. The web
application is healthy, but the background worker cannot connect to Redis.
There were no known deployments.

Investigate the running services from the incident workstation and restore a
successful write through the worker's configured Redis connection.

Use the Docker CLI to inspect and repair the sibling service containers. Do not
replace Redis or the worker, and do not bypass authentication. Repair the
deployed services and make the recovery survive Redis and worker restarts.
