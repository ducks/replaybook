# One-way initiation

Only alpha may initiate new connections to beta. Established replies are allowed.

See the pack README for execution and evidence details.

Version 2 enforces the peer ingress policy at the controller-owned wire, outside both guests. IPv4 TCP connections may be initiated to TCP 8765; for one-way initiation only alpha may initiate. IPv4 ICMP and ARP are allowed; other traffic is blocked. The wire records blocked-frame counts. Guest firewall edits cannot bypass the wire policy. Version 1 results do not have this enforcement.
