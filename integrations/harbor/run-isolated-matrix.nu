#!/usr/bin/env nu

def prompt-secret [name: string, prompt: string] {
    let existing = ($env | get --optional $name | default "")
    if not ($existing | is-empty) {
        return $existing
    }

    let value = (input --suppress-output $"($prompt): ")
    if ($value | is-empty) {
        error make {msg: $"($name) cannot be empty"}
    }
    $value
}

def run-worker [job: record, runner: string, claude_token: string, openrouter_key: string] {
    print $"[matrix] starting ($job.run_id) on SSH port ($job.port)"

    let worker = match $job.agent {
        "claude" => {
            with-env {
                REPLAYBOOK_WORKER_SSH_PORT: ($job.port | into string)
                CLAUDE_CODE_OAUTH_TOKEN: $claude_token
            } {
                ^bash $runner ...$job.worker_options -- --config $job.config --yes | complete
            }
        }
        "claux" => {
            with-env {
                REPLAYBOOK_WORKER_SSH_PORT: ($job.port | into string)
                OPENROUTER_API_KEY: $openrouter_key
            } {
                ^bash $runner ...$job.worker_options -- --config $job.config --yes | complete
            }
        }
        _ => {
            with-env {
                REPLAYBOOK_WORKER_SSH_PORT: ($job.port | into string)
            } {
                ^bash $runner ...$job.worker_options -- --config $job.config --yes | complete
            }
        }
    }

    $"($worker.stdout)($worker.stderr)" | save --force $job.log_file
    if $worker.exit_code == 0 {
        print $"[matrix] passed ($job.run_id)"
    } else {
        print --stderr $"[matrix] failed ($job.run_id); see ($job.log_file)"
    }

    {
        run_id: $job.run_id
        exit_code: $worker.exit_code
        log_file: $job.log_file
    }
}

def main [
    --attempts: int = 3      # Attempts per agent.
    --concurrency: int = 2   # Maximum simultaneous VMs.
    --base-port: int = 22300 # First forwarded worker SSH port.
    --oracle                 # Run only oracle attempts as a worker-pool smoke test.
] {
    if $attempts <= 0 {
        error make {msg: "--attempts must be a positive integer"}
    }
    if $concurrency <= 0 {
        error make {msg: "--concurrency must be a positive integer"}
    }
    if $base_port <= 0 or $base_port > 65535 {
        error make {msg: "--base-port must be an integer from 1 to 65535"}
    }

    let script_dir = ($env.FILE_PWD | path expand)
    let repo_dir = ([$script_dir "../.."] | path join | path expand)
    let runner = ([$script_dir "run-isolated.sh"] | path join)

    let agents = if $oracle {
        [
            {
                agent: oracle
                config: integrations/harbor/jobs/oracle-smoke.yaml
                auth_options: []
            }
        ]
    } else {
        [
            {
                agent: codex
                config: integrations/harbor/jobs/codex-single.yaml
                auth_options: [--codex-auth]
            }
            {
                agent: claude
                config: integrations/harbor/jobs/claude-single.yaml
                auth_options: [--env CLAUDE_CODE_OAUTH_TOKEN]
            }
            {
                agent: claux
                config: integrations/harbor/jobs/claux-single.yaml
                auth_options: [--env OPENROUTER_API_KEY]
            }
        ]
    }

    let expected_trials = ($attempts * ($agents | length))
    let last_port = ($base_port + $expected_trials - 1)
    if $last_port > 65535 {
        error make {msg: "worker port range ends above 65535"}
    }

    for port in $base_port..$last_port {
        let listeners = (^ss -ltn $"sport = :($port)" | lines | skip 1)
        if not ($listeners | is-empty) {
            error make {msg: $"worker SSH port is already in use: ($port)"}
        }
    }

    let claude_token = if $oracle {
        ""
    } else {
        prompt-secret CLAUDE_CODE_OAUTH_TOKEN "Claude OAuth token"
    }
    let openrouter_key = if $oracle {
        ""
    } else {
        prompt-secret OPENROUTER_API_KEY "OpenRouter API key"
    }

    if not $oracle {
        let codex_auth = (
            $env.REPLAYBOOK_CODEX_AUTH_FILE?
            | default "~/.codex/auth.json"
            | path expand
        )
        if not ($codex_auth | path exists) {
            error make {msg: $"Codex auth is missing: ($codex_auth)"}
        }
    }

    let timestamp = (date now | date to-timezone "+0000" | format date "%Y-%m-%d__%H-%M-%S")
    let suffix = (random chars --length 6)
    let matrix_dir = ([$repo_dir jobs $"isolated-matrix-($timestamp).($suffix)"] | path join)
    let logs_dir = ([$matrix_dir logs] | path join)
    let runs_dir = ([$matrix_dir runs] | path join)
    mkdir $logs_dir $runs_dir

    let jobs = (
        $agents
        | each {|agent|
            1..$attempts
            | each {|attempt|
                let run_id = $"($agent.agent)-($attempt)"
                let output_dir = ([$runs_dir $run_id] | path join)
                {
                    agent: $agent.agent
                    attempt: $attempt
                    run_id: $run_id
                    port: 0
                    config: $agent.config
                    worker_options: [--output-dir $output_dir ...$agent.auth_options]
                    log_file: ([$logs_dir $"($run_id).log"] | path join)
                }
            }
        }
        | flatten
        | enumerate
        | each {|entry| $entry.item | update port ($base_port + $entry.index) }
    )

    print $"[matrix] results: ($matrix_dir)"
    print $"[matrix] launching ($expected_trials) trials with at most ($concurrency) VMs"
    let worker_results = (
        $jobs
        | par-each --keep-order --threads $concurrency {|job|
            run-worker $job $runner $claude_token $openrouter_key
        }
    )

    let result_pattern = ([$runs_dir "*" jobs "*" result.json] | path join)
    let result_files = (glob $result_pattern | sort)
    let summary_file = ([$matrix_dir summary.json] | path join)
    let generated_at = (date now | date to-timezone "+0000" | format date "%Y-%m-%dT%H:%M:%SZ")
    let jq_filter = r#'
      {
        schema_version: 1,
        generated_at: $generated_at,
        expected_trials: $expected_trials,
        received_jobs: length,
        totals: {
          completed: ([.[].stats.n_completed_trials // 0] | add // 0),
          errored: ([.[].stats.n_errored_trials // 0] | add // 0),
          input_tokens: ([.[].stats.n_input_tokens // 0] | add // 0),
          cache_tokens: ([.[].stats.n_cache_tokens // 0] | add // 0),
          output_tokens: ([.[].stats.n_output_tokens // 0] | add // 0),
          known_cost_usd: ([.[].stats.cost_usd // 0] | add // 0),
          cost_reported_jobs: ([.[] | select(.stats.cost_usd != null)] | length)
        },
        runs: [
          .[] |
          (.stats.evals | to_entries[0]) as $eval |
          {
            job_id: .id,
            agent_model: $eval.key,
            trials: (.stats.n_completed_trials // 0),
            errors: (.stats.n_errored_trials // 0),
            mean: ($eval.value.metrics[0].mean // null),
            input_tokens: .stats.n_input_tokens,
            cache_tokens: .stats.n_cache_tokens,
            output_tokens: .stats.n_output_tokens,
            cost_usd: .stats.cost_usd
          }
        ]
      }
      | .by_agent = (
          .runs
          | group_by(.agent_model)
          | map({
              agent_model: .[0].agent_model,
              trials: ([.[].trials] | add // 0),
              errors: ([.[].errors] | add // 0),
              mean: (
                ([.[] | select(.mean != null) | (.mean * .trials)] | add // 0)
                / ([.[] | select(.mean != null) | .trials] | add // 1)
              ),
              input_tokens: ([.[].input_tokens // 0] | add // 0),
              cache_tokens: ([.[].cache_tokens // 0] | add // 0),
              output_tokens: ([.[].output_tokens // 0] | add // 0),
              known_cost_usd: ([.[].cost_usd // 0] | add // 0),
              cost_reported_jobs: ([.[] | select(.cost_usd != null)] | length)
            })
        )
    '#

    let summary_json = if ($result_files | is-empty) {
        ^jq --null-input --arg "generated_at" $generated_at --argjson "expected_trials" ($expected_trials | into string) '{schema_version: 1, generated_at: $generated_at, expected_trials: $expected_trials, received_jobs: 0, totals: {completed: 0, errored: 0}, runs: [], by_agent: []}'
    } else {
        ^jq --slurp --arg "generated_at" $generated_at --argjson "expected_trials" ($expected_trials | into string) $jq_filter ...$result_files
    }
    $summary_json | save --force $summary_file

    let summary = (open $summary_file)
    print ""
    print ($summary.by_agent | select agent_model trials errors mean known_cost_usd)
    print $"[matrix] summary: ($summary_file)"

    let worker_failures = ($worker_results | where exit_code != 0 | length)
    if (
        $worker_failures > 0
        or $summary.received_jobs != $expected_trials
        or $summary.totals.errored > 0
    ) {
        error make {
            msg: $"matrix incomplete: ($worker_failures) workers failed, ($summary.received_jobs)/($expected_trials) results received, ($summary.totals.errored) trials errored"
        }
    }

    print $"[matrix] all ($expected_trials) isolated trials completed"
}
