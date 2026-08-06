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

compose_label() {
  local container="$1"
  local label="$2"

  docker inspect --format "{{index .Config.Labels \"$label\"}}" "$container"
}

compose_contract_violation() {
  local container="$1"
  local expected_service="$2"
  local labels aliases label value actual_image
  local required_labels=(
    com.docker.compose.config-hash
    com.docker.compose.container-number
    com.docker.compose.depends_on
    com.docker.compose.image
    com.docker.compose.oneoff
    com.docker.compose.project
    com.docker.compose.project.config_files
    com.docker.compose.project.working_dir
    com.docker.compose.service
    com.docker.compose.version
  )

  if ! labels="$(docker inspect --format '{{json .Config.Labels}}' "$container")"; then
    printf '%s\n' "$expected_service container cannot be inspected"
    return 0
  fi

  for label in "${required_labels[@]}"; do
    if [[ "$labels" != *"\"$label\":"* ]]; then
      printf '%s\n' "$expected_service container is missing Compose label $label"
      return 0
    fi
  done

  value="$(compose_label "$container" com.docker.compose.service)"
  if [[ "$value" != "$expected_service" ]]; then
    printf '%s\n' "$expected_service container has Compose service label $value"
    return 0
  fi

  value="$(compose_label "$container" com.docker.compose.oneoff)"
  if [[ "$value" != "False" ]]; then
    printf '%s\n' "$expected_service container is marked as a Compose one-off"
    return 0
  fi

  value="$(compose_label "$container" com.docker.compose.config-hash)"
  if [[ ! "$value" =~ ^[[:xdigit:]]{64}$ ]]; then
    printf '%s\n' "$expected_service container has an invalid Compose config hash"
    return 0
  fi

  value="$(compose_label "$container" com.docker.compose.image)"
  if [[ ! "$value" =~ ^sha256:[[:xdigit:]]{64}$ ]]; then
    printf '%s\n' "$expected_service container has an invalid Compose image identity"
    return 0
  fi
  actual_image="$(docker inspect --format '{{.Image}}' "$container")"
  if [[ "$value" != "$actual_image" ]]; then
    printf '%s\n' "$expected_service container's Compose image identity is stale"
    return 0
  fi

  value="$(compose_label "$container" com.docker.compose.container-number)"
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' "$expected_service container has an invalid Compose replica number"
    return 0
  fi

  for label in \
    com.docker.compose.project \
    com.docker.compose.project.config_files \
    com.docker.compose.project.working_dir \
    com.docker.compose.version; do
    value="$(compose_label "$container" "$label")"
    if [[ -z "$value" ]]; then
      printf '%s\n' "$expected_service container has an empty Compose label $label"
      return 0
    fi
  done

  aliases="$(
    docker inspect --format \
      '{{range .NetworkSettings.Networks}}{{range .Aliases}}{{println .}}{{end}}{{end}}' \
      "$container"
  )"
  if ! grep -Fqx "$expected_service" <<<"$aliases"; then
    printf '%s\n' "$expected_service container is missing its service network alias"
    return 0
  fi

  return 1
}

compose_project() {
  compose_label "$1" com.docker.compose.project
}
