The API health endpoint is green, but customers cannot access authenticated
routes after this morning's credential rotation. The old credential must no
longer grant access. There are also recent cache warnings in the application
logs, though no application deployment was planned.

Investigate from the incident workstation and repair the deployed service.
Preserve authentication, keep the existing service topology, and make the fix
survive an application restart.
