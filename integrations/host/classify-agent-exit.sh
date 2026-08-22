#!/usr/bin/env bash
set -euo pipefail

status="${1:?usage: classify-agent-exit.sh STATUS CONSOLE_LOG}"
console_log="${2:?usage: classify-agent-exit.sh STATUS CONSOLE_LOG}"

if [[ "$status" == "255" && -f "$console_log" ]] &&
  grep -aEq 'reboot: Restarting system|systemd-shutdown.*Rebooting' "$console_log"; then
  printf '%s\n' "agent_rebooted_host"
elif [[ "$status" == "137" && -f "$console_log" ]] &&
  grep -aEq 'Out of memory: Killed process .*\((claux|opencode|codex)\)|oom-kill:.*task=(claux|opencode|codex)' \
    "$console_log"; then
  printf '%s\n' "guest_out_of_memory"
fi
