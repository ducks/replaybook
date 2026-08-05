Checkout is technically available, but requests now exceed the 800 ms latency
SLO and clients are timing out. The primary pricing dependency is undergoing
maintenance; the healthy fallback is expected to carry traffic during this
window. General application health remains green.

Investigate from the incident workstation and restore checkout latency. Keep
the application, primary, and fallback services in the request path, and make
the repair survive service restarts.
