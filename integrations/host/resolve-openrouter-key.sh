#!/usr/bin/env bash
set -euo pipefail

# Explicit credentials remain authoritative for CI and per-run overrides.
if [[ -n "${REPLAYBOOK_OPENAI_API_KEY:-}" ]]; then
  printf '%s\n' "$REPLAYBOOK_OPENAI_API_KEY"
  exit 0
fi
if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  printf '%s\n' "$OPENROUTER_API_KEY"
  exit 0
fi

candidates=()
if [[ -n "${REPLAYBOOK_CLAUX_AUTH_BINARY:-}" ]]; then
  candidates+=("$REPLAYBOOK_CLAUX_AUTH_BINARY")
fi
if command -v claux >/dev/null 2>&1; then
  candidates+=("$(command -v claux)")
fi
if [[ -n "${REPLAYBOOK_HOST_CLAUX_BINARY:-}" ]]; then
  candidates+=("$REPLAYBOOK_HOST_CLAUX_BINARY")
fi

previous=""
for claux in "${candidates[@]}"; do
  [[ "$claux" != "$previous" ]] || continue
  previous="$claux"
  [[ -x "$claux" ]] || continue
  if key="$("$claux" auth token openrouter 2>/dev/null)" && [[ -n "$key" ]]; then
    printf '%s\n' "$key"
    exit 0
  fi
done

exit 1
