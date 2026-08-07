Checkout confirmation requests are accepted, but background processing is
failing and confirmations remain pending. The web health endpoint is healthy,
and the background worker appears to be running. This began after a routine
application deployment.

Investigate this Linux host and durably repair the deployed service. Preserve
all existing confirmation jobs and the current systemd service topology. The
repair must survive service restarts and a host reboot.

Do not reboot, shut down, or replace the host yourself. The external verifier
will perform the reboot after your session ends.
