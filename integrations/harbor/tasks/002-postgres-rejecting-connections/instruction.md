# Restore PostgreSQL connections

The application is receiving PostgreSQL authentication failures. The database
container is running, but TCP connections to the appdb database are rejected.

Investigate the database container from the incident workstation and restore a
real TCP connection with:

    host: db
    port: 5432
    user: postgres
    database: appdb
    password: password

Use the Docker CLI to inspect and repair the sibling database container. Do not
replace the database or bypass it with another service; repair the deployed
PostgreSQL configuration and make the fix survive a restart.
