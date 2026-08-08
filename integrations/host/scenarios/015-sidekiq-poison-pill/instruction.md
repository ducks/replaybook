Checkout confirmation requests are accepted, but background processing has
stopped and valid confirmations are accumulating. The web health endpoint is
healthy, and the Sidekiq service appears to be running.

Investigate this Linux host and durably repair the deployed service. Preserve
and process all valid confirmation jobs. Do not discard the job that caused the
incident: quarantine it with enough identity to investigate later. Future jobs
with the same bad payload must be quarantined without blocking valid work.
Preserve the current systemd service topology. The repair must survive service
restarts and a host reboot.

Do not reboot, shut down, or replace the host yourself. The external verifier
will perform the reboot after your session ends.
