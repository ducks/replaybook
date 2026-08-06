#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/tasks/012-retry-storm/tests/topology.sh"

getent() {
  [[ "$1" == "hosts" ]] || return 2
  [[ "$2" != "app" ]]
}

unresolved="$(first_unresolved_service primary app fallback)"
[[ "$unresolved" == "app" ]]

getent() {
  [[ "$1" == "hosts" ]] || return 2
  return 0
}

if first_unresolved_service app primary fallback >/dev/null; then
  echo "all-resolvable topology was classified as changed" >&2
  exit 1
fi

declare -A compose_labels=(
  [com.docker.compose.config-hash]="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  [com.docker.compose.container-number]="1"
  [com.docker.compose.depends_on]="primary:service_started:false"
  [com.docker.compose.image]="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  [com.docker.compose.oneoff]="False"
  [com.docker.compose.project]="retry-storm"
  [com.docker.compose.project.config_files]="/workspace/docker-compose.yaml"
  [com.docker.compose.project.working_dir]="/workspace"
  [com.docker.compose.service]="app"
  [com.docker.compose.version]="2.29.7"
)

full_labels_json='{"com.docker.compose.config-hash":"a","com.docker.compose.container-number":"1","com.docker.compose.depends_on":"primary:service_started:false","com.docker.compose.image":"sha256:b","com.docker.compose.oneoff":"False","com.docker.compose.project":"retry-storm","com.docker.compose.project.config_files":"/workspace/docker-compose.yaml","com.docker.compose.project.working_dir":"/workspace","com.docker.compose.service":"app","com.docker.compose.version":"2.29.7"}'
three_labels_json='{"com.docker.compose.container-number":"1","com.docker.compose.project":"retry-storm","com.docker.compose.service":"app"}'
mock_labels_json="$full_labels_json"
mock_aliases=$'retry-storm-app-1\napp'
mock_actual_image="${compose_labels[com.docker.compose.image]}"

docker() {
  [[ "$1" == "inspect" && "$2" == "--format" ]] || return 2
  local format="$3"

  if [[ "$format" == '{{json .Config.Labels}}' ]]; then
    printf '%s\n' "$mock_labels_json"
    return 0
  fi
  if [[ "$format" == *'.NetworkSettings.Networks'* ]]; then
    printf '%s\n' "$mock_aliases"
    return 0
  fi
  if [[ "$format" == '{{.Image}}' ]]; then
    printf '%s\n' "$mock_actual_image"
    return 0
  fi
  if [[ "$format" =~ \"([^\"]+)\" ]]; then
    printf '%s\n' "${compose_labels[${BASH_REMATCH[1]}]:-}"
    return 0
  fi
  return 2
}

if compose_contract_violation app-container app >/dev/null; then
  echo "valid Compose service was classified as changed" >&2
  exit 1
fi

mock_labels_json="$three_labels_json"
violation="$(compose_contract_violation app-container app)"
[[ "$violation" == *"missing Compose label com.docker.compose.config-hash"* ]]

mock_labels_json="$full_labels_json"
mock_aliases="retry-storm-app-1"
violation="$(compose_contract_violation app-container app)"
[[ "$violation" == *"missing its service network alias"* ]]

mock_aliases=$'retry-storm-app-1\napp'
mock_actual_image="sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
violation="$(compose_contract_violation app-container app)"
[[ "$violation" == *"Compose image identity is stale"* ]]
