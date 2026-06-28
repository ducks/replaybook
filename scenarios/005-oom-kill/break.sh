#!/usr/bin/env bash
# The fault is in the compose file - mem_limit is too low for CACHE_MB.
# Nothing to inject; the OOM killer handles it.
exit 0
