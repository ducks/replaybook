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

def run-worker [
    job: record,
    runner_source: string,
    script_dir: string,
    repo_dir: string,
    claude_token: string,
    openrouter_key: string,
] {
    print $"[matrix] starting ($job.run_id) on SSH port ($job.port)"

    let base_env = {
        REPLAYBOOK_WORKER_SSH_PORT: ($job.port | into string)
        REPLAYBOOK_HARBOR_DIR: $script_dir
        REPLAYBOOK_REPO_DIR: $repo_dir
    }
    let worker_env = match $job.agent {
        "claude" => ($base_env | insert CLAUDE_CODE_OAUTH_TOKEN $claude_token)
        "claux" => ($base_env | insert OPENROUTER_API_KEY $openrouter_key)
        _ => $base_env
    }
    let worker = with-env $worker_env {
        ^bash -c $runner_source run-isolated.sh ...$job.worker_options -- --config $job.config --yes | complete
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
    --agent: string = all    # Agent to run: all, codex, claude, or claux.
    --scenario: string       # Run one Harbor scenario directory name.
    --scenario-set: string   # Named set: core, hard, development, heldout, or all.
    --claux-model: string = deepseek/deepseek-v4-flash # OpenRouter model used by Claux.
    --all-scenarios          # Run every configured scenario (legacy alias for --scenario-set all).
    --list-scenarios         # Print the selected scenarios without launching workers.
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
    let runner_source = (open --raw $runner)

    let scenario_configs = {
        "001-nginx-502": {
            codex: integrations/harbor/jobs/codex-single.yaml
            claude: integrations/harbor/jobs/claude-single.yaml
            claux: integrations/harbor/jobs/claux-single.yaml
            oracle: integrations/harbor/jobs/oracle-smoke.yaml
        }
        "002-postgres-rejecting-connections": {
            codex: integrations/harbor/jobs/codex-002.yaml
            claude: integrations/harbor/jobs/claude-002.yaml
            claux: integrations/harbor/jobs/claux-002.yaml
            oracle: integrations/harbor/jobs/oracle-002.yaml
        }
        "003-missing-env-var": {
            codex: integrations/harbor/jobs/codex-003.yaml
            claude: integrations/harbor/jobs/claude-003.yaml
            claux: integrations/harbor/jobs/claux-003.yaml
            oracle: integrations/harbor/jobs/oracle-003.yaml
        }
        "004-disk-full": {
            codex: integrations/harbor/jobs/codex-004.yaml
            claude: integrations/harbor/jobs/claude-004.yaml
            claux: integrations/harbor/jobs/claux-004.yaml
            oracle: integrations/harbor/jobs/oracle-004.yaml
        }
        "005-oom-kill": {
            codex: integrations/harbor/jobs/codex-005.yaml
            claude: integrations/harbor/jobs/claude-005.yaml
            claux: integrations/harbor/jobs/claux-005.yaml
            oracle: integrations/harbor/jobs/oracle-005.yaml
        }
        "006-sidekiq-cant-connect": {
            codex: integrations/harbor/jobs/codex-006.yaml
            claude: integrations/harbor/jobs/claude-006.yaml
            claux: integrations/harbor/jobs/claux-006.yaml
            oracle: integrations/harbor/jobs/oracle-006.yaml
        }
        "007-packet-loss": {
            codex: integrations/harbor/jobs/codex-007.yaml
            claude: integrations/harbor/jobs/claude-007.yaml
            claux: integrations/harbor/jobs/claux-007.yaml
            oracle: integrations/harbor/jobs/oracle-007.yaml
        }
        "008-connection-pool-exhaustion": {
            codex: integrations/harbor/jobs/codex-008.yaml
            claude: integrations/harbor/jobs/claude-008.yaml
            claux: integrations/harbor/jobs/claux-008.yaml
            oracle: integrations/harbor/jobs/oracle-008.yaml
        }
        "009-phantom-backend": {
            codex: integrations/harbor/jobs/codex-009.yaml
            claude: integrations/harbor/jobs/claude-009.yaml
            claux: integrations/harbor/jobs/claux-009.yaml
            oracle: integrations/harbor/jobs/oracle-009.yaml
        }
        "010-stale-auth-secret": {
            codex: integrations/harbor/jobs/codex-010.yaml
            claude: integrations/harbor/jobs/claude-010.yaml
            claux: integrations/harbor/jobs/claux-010.yaml
            oracle: integrations/harbor/jobs/oracle-010.yaml
        }
        "011-partial-rollout": {
            codex: integrations/harbor/jobs/codex-011.yaml
            claude: integrations/harbor/jobs/claude-011.yaml
            claux: integrations/harbor/jobs/claux-011.yaml
            oracle: integrations/harbor/jobs/oracle-011.yaml
        }
        "012-retry-storm": {
            codex: integrations/harbor/jobs/codex-012.yaml
            claude: integrations/harbor/jobs/claude-012.yaml
            claux: integrations/harbor/jobs/claux-012.yaml
            oracle: integrations/harbor/jobs/oracle-012.yaml
        }
    }
    let scenario_sets = (open ([$script_dir scenario-sets.json] | path join))
    if $scenario_sets.schema_version != 1 {
        error make {msg: "unsupported scenario set schema version"}
    }
    let core_scenarios = $scenario_sets.core
    let hard_scenarios = $scenario_sets.hard
    let development_scenarios = $scenario_sets.development
    let heldout_scenarios = $scenario_sets.heldout
    let scenario_names = [$core_scenarios $hard_scenarios] | flatten

    if $all_scenarios and $scenario_set != null {
        error make {msg: "--all-scenarios cannot be combined with --scenario-set"}
    }
    if $all_scenarios and $scenario != null {
        error make {msg: "--all-scenarios cannot be combined with --scenario"}
    }
    if $scenario_set != null and $scenario != null {
        error make {msg: "--scenario-set cannot be combined with --scenario"}
    }

    let selected_scenarios = if $all_scenarios {
        $scenario_names
    } else if $scenario_set != null {
        match $scenario_set {
            "core" => $core_scenarios
            "hard" => $hard_scenarios
            "development" => $development_scenarios
            "heldout" => $heldout_scenarios
            "all" => $scenario_names
            _ => { error make {msg: "--scenario-set must be one of: core, hard, development, heldout, all"} }
        }
    } else {
        let selected_scenario = $scenario | default "001-nginx-502"
        if ($scenario_configs | get --optional $selected_scenario) == null {
            error make {msg: $"unknown scenario: ($selected_scenario)"}
        }
        [$selected_scenario]
    }
    let selected_set_name = if $all_scenarios {
        "all"
    } else if $scenario_set != null {
        $scenario_set
    } else {
        "single"
    }

    if $list_scenarios {
        $selected_scenarios | each {|selected| print $selected }
        return
    }

    let comparison_agents = (
        $selected_scenarios
        | each {|scenario_name|
            let configs = ($scenario_configs | get $scenario_name)
            [
                {
                    scenario: $scenario_name
                    agent: codex
                    config: ($configs | get codex)
                    auth_options: [--codex-auth]
                }
                {
                    scenario: $scenario_name
                    agent: claude
                    config: ($configs | get claude)
                    auth_options: [--env CLAUDE_CODE_OAUTH_TOKEN]
                }
                {
                    scenario: $scenario_name
                    agent: claux
                    config: ($configs | get claux)
                    auth_options: [--env OPENROUTER_API_KEY]
                }
            ]
        }
        | flatten
    )
    let agents = if $oracle {
        if $agent != "all" {
            error make {msg: "--oracle cannot be combined with --agent"}
        }
        $selected_scenarios
        | each {|scenario_name|
            {
                scenario: $scenario_name
                agent: oracle
                config: (($scenario_configs | get $scenario_name) | get oracle)
                auth_options: []
            }
        }
    } else if $agent == "all" {
        $comparison_agents
    } else {
        let selected = ($comparison_agents | where agent == $agent)
        if ($selected | is-empty) {
            error make {msg: "--agent must be one of: all, codex, claude, claux"}
        }
        $selected
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

    let needs_claude = ($agents | any {|candidate| $candidate.agent == "claude" })
    let needs_claux = ($agents | any {|candidate| $candidate.agent == "claux" })
    let needs_codex = ($agents | any {|candidate| $candidate.agent == "codex" })
    let claude_token = if $needs_claude {
        prompt-secret CLAUDE_CODE_OAUTH_TOKEN "Claude OAuth token"
    } else {
        ""
    }
    let openrouter_key = if $needs_claux {
        prompt-secret OPENROUTER_API_KEY "OpenRouter API key"
    } else {
        ""
    }

    if $needs_codex {
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
    let configs_dir = ([$matrix_dir configs] | path join)
    mkdir $logs_dir $runs_dir $configs_dir

    let benchmark = {
        suite: replaybook-harbor-v1
        replaybook_commit: (^git -C $repo_dir rev-parse HEAD | str trim)
        scenario_set: $selected_set_name
        scenarios: $selected_scenarios
        attempts: $attempts
        agent: (if $oracle { "oracle" } else { $agent })
        claux_model: (if $needs_claux { $claux_model } else { null })
    }
    $benchmark | save --force ([$matrix_dir benchmark.json] | path join)

    let configured_agents = (
        $agents
        | each {|agent|
            if $agent.agent == "claux" {
                let source_config = ([$repo_dir $agent.config] | path join)
                let generated_config = ([$configs_dir $"($agent.scenario)-claux.yaml"] | path join)
                open $source_config
                    | update agents.0.model_name $claux_model
                    | to yaml
                    | save --force $generated_config
                $agent
                    | update config /root/worker/job.yaml
                    | insert job_config $generated_config
            } else {
                $agent | insert job_config null
            }
        }
    )

    let jobs = (
        $configured_agents
        | each {|agent|
            1..$attempts
            | each {|attempt|
                let run_id = $"($agent.scenario)-($agent.agent)-($attempt)"
                let output_dir = ([$runs_dir $run_id] | path join)
                let config_options = if $agent.job_config == null {
                    []
                } else {
                    [--job-config $agent.job_config]
                }
                {
                    agent: $agent.agent
                    attempt: $attempt
                    run_id: $run_id
                    port: 0
                    config: $agent.config
                    worker_options: [--output-dir $output_dir ...$config_options ...$agent.auth_options]
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
            run-worker $job $runner_source $script_dir $repo_dir $claude_token $openrouter_key
        }
    )

    let result_pattern = ([$runs_dir "*" jobs "*" result.json] | path join)
    let result_files = (glob $result_pattern | sort)
    let summary_file = ([$matrix_dir summary.json] | path join)
    let summary_filter = ([$script_dir matrix-summary.jq] | path join)
    let generated_at = (date now | date to-timezone "+0000" | format date "%Y-%m-%dT%H:%M:%SZ")
    let failure_details = (
        glob ([$runs_dir "*" jobs "*" "*" verifier failure-category.txt] | path join)
        | reduce --fold {} {|category_file, details|
            let trial_dir = ($category_file | path dirname | path dirname)
            let trial_id = ($trial_dir | path basename)
            let message_file = ([$trial_dir verifier test-stdout.txt] | path join)
            let message = if ($message_file | path exists) {
                open --raw $message_file | str trim
            } else {
                null
            }
            $details | upsert $trial_id {
                category: (open --raw $category_file | str trim)
                message: $message
            }
        }
    )

    let summary_json = if ($result_files | is-empty) {
        ^jq --null-input --arg "generated_at" $generated_at --argjson "expected_trials" ($expected_trials | into string) --argjson "benchmark" ($benchmark | to json) '{schema_version: 3, generated_at: $generated_at, benchmark: $benchmark, expected_trials: $expected_trials, received_jobs: 0, totals: {completed: 0, errored: 0, pending: 0, cancelled: 0}, runs: [], failure_categories: [], by_agent: [], by_scenario: [], by_scenario_agent: []}'
    } else {
        ^jq --slurp --arg "generated_at" $generated_at --argjson "expected_trials" ($expected_trials | into string) --argjson "failure_details" ($failure_details | to json) --argjson "benchmark" ($benchmark | to json) --from-file $summary_filter ...$result_files
    }
    $summary_json | save --force $summary_file

    let summary = (open $summary_file)
    print ""
    print ($summary.by_agent | select agent_model trials errors mean mean_duration_seconds median_duration_seconds known_cost_usd)
    print ""
    print ($summary.by_scenario_agent | select scenario agent_model trials errors mean median_duration_seconds known_cost_usd)
    if not ($summary.failure_categories | is-empty) {
        print ""
        print ($summary.failure_categories | select category count)
    }
    print $"[matrix] summary: ($summary_file)"

    let worker_failures = ($worker_results | where exit_code != 0 | length)
    if (
        $worker_failures > 0
        or $summary.received_jobs != $expected_trials
        or $summary.totals.completed != $expected_trials
        or $summary.totals.errored > 0
    ) {
        error make {
            msg: $"matrix incomplete: ($worker_failures) workers failed, ($summary.received_jobs)/($expected_trials) results received, ($summary.totals.completed)/($expected_trials) trials completed, ($summary.totals.errored) trials errored"
        }
    }

    print $"[matrix] all ($expected_trials) isolated trials completed"
}
