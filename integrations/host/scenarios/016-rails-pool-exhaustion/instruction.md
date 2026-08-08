Checkout requests intermittently fail during ordinary concurrent traffic. The
Rails health endpoint remains healthy, PostgreSQL is running, and individual
requests may succeed when traffic is low. This began after application server
concurrency was increased.

Investigate this Linux host and durably repair the deployed service. Recover
the failed checkout requests and ensure the application can sustain its
configured concurrency without database connection timeouts. Preserve the
current systemd service topology. The repair must survive service restarts and
a host reboot.

Do not reboot, shut down, or replace the host yourself. The external verifier
will perform the reboot after your session ends.
