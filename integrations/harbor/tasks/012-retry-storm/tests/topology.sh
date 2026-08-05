#!/usr/bin/env bash

first_unresolved_service() {
  local service

  for service in "$@"; do
    if ! getent hosts "$service" >/dev/null 2>&1; then
      printf '%s\n' "$service"
      return 0
    fi
  done

  return 1
}
