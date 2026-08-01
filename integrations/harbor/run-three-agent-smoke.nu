#!/usr/bin/env nu

if not ("integrations/harbor/jobs/three-agent-smoke.yaml" | path exists) {
    error make {
        msg: "run this script from the Replaybook repository root"
    }
}

if not ("~/.codex/auth.json" | path expand | path exists) {
    error make {
        msg: "Codex auth is missing; log in with Codex before running the evaluation"
    }
}

let claude_token = if ($env.CLAUDE_CODE_OAUTH_TOKEN? | is-empty) {
    input --suppress-output "Claude OAuth token: "
} else {
    $env.CLAUDE_CODE_OAUTH_TOKEN
}
if ($claude_token | is-empty) {
    error make {
        msg: "Claude OAuth token cannot be empty; generate one with `claude setup-token`"
    }
}

let openrouter_key = if ($env.OPENROUTER_API_KEY? | is-empty) {
    input --suppress-output "OpenRouter API key: "
} else {
    $env.OPENROUTER_API_KEY
}
if ($openrouter_key | is-empty) {
    error make {
        msg: "OpenRouter API key cannot be empty"
    }
}

let buildx_bin = (
    nix-shell -p docker-buildx --run 'readlink -f $(command -v docker-buildx)'
    | str trim
)

let docker_config = (
    mktemp -d -t replaybook-docker-config.XXXXXX
    | str trim
)

mkdir $"($docker_config)/cli-plugins"
ln -s $buildx_bin $"($docker_config)/cli-plugins/docker-buildx"
ln -s /usr/lib/docker/cli-plugins/docker-compose $"($docker_config)/cli-plugins/docker-compose"

with-env {
    DOCKER_CONFIG: $docker_config
    PYTHONPATH: (pwd | path expand)
    CLAUDE_CODE_OAUTH_TOKEN: $claude_token
    OPENROUTER_API_KEY: $openrouter_key
    HARBOR_TELEMETRY: "off"
} {
    ~/.local/share/pipx/venvs/harbor/bin/harbor run --config integrations/harbor/jobs/three-agent-smoke.yaml --yes
}
