Checkout requests began failing intermittently after a routine rollout. General
health checks remain green. The service is intentionally deployed with two app
replicas and both must remain in rotation.

Investigate from the incident workstation, restore consistent checkout
behavior, and make the repair survive service restarts. Do not remove a replica
or bypass the load balancer.
