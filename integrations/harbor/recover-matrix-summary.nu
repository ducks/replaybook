#!/usr/bin/env nu

# Rebuild a matrix summary from worker results after a reporting failure.

def main [matrix_dir: string] {
    let matrix_dir = ($matrix_dir | path expand)
    let runs_dir = ([$matrix_dir runs] | path join)
    if not ($runs_dir | path exists) {
        error make {msg: $"matrix runs directory does not exist: ($runs_dir)"}
    }

    let result_files = (glob ([$runs_dir "*" jobs "*" result.json] | path join) | sort)
    if ($result_files | is-empty) {
        error make {msg: $"no worker result files found under: ($runs_dir)"}
    }

    let generated_at = (date now | date to-timezone "+0000" | format date "%Y-%m-%dT%H:%M:%SZ")
    let jq_filter = r#'
      def seconds:
        if . == null then null
        else (
          sub("Z$"; "")
          | sub("\\.[0-9]+$"; "")
          | . + "Z"
          | fromdateiso8601
        )
        end;
      {
        schema_version: 1,
        generated_at: $generated_at,
        expected_trials: ([.[].stats | (.n_completed_trials // 0) + (.n_errored_trials // 0) + (.n_pending_trials // 0) + (.n_cancelled_trials // 0)] | add // 0),
        received_jobs: length,
        totals: {
          completed: ([.[].stats.n_completed_trials // 0] | add // 0),
          errored: ([.[].stats.n_errored_trials // 0] | add // 0),
          pending: ([.[].stats.n_pending_trials // 0] | add // 0),
          cancelled: ([.[].stats.n_cancelled_trials // 0] | add // 0),
          input_tokens: ([.[].stats.n_input_tokens // 0] | add // 0),
          cache_tokens: ([.[].stats.n_cache_tokens // 0] | add // 0),
          output_tokens: ([.[].stats.n_output_tokens // 0] | add // 0),
          known_cost_usd: ([.[].stats.cost_usd // 0] | add // 0),
          cost_reported_jobs: ([.[] | select(.stats.cost_usd != null)] | length)
        },
        runs: [
          .[] |
          (.stats.evals | to_entries[0]) as $eval |
          (.started_at | seconds) as $started |
          (.finished_at | seconds) as $finished |
          (.agent_execution.started_at | seconds) as $agent_started |
          (.agent_execution.finished_at | seconds) as $agent_finished |
          {
            job_id: .id,
            agent_model: $eval.key,
            trials: (.stats.n_completed_trials // 0),
            errors: (.stats.n_errored_trials // 0),
            mean: ($eval.value.metrics[0].mean // null),
            input_tokens: .stats.n_input_tokens,
            cache_tokens: .stats.n_cache_tokens,
            output_tokens: .stats.n_output_tokens,
            cost_usd: .stats.cost_usd,
            duration_seconds: (if ($started != null and $finished != null) then $finished - $started else null end),
            agent_duration_seconds: (if ($agent_started != null and $agent_finished != null) then $agent_finished - $agent_started else null end)
          }
        ]
      }
      | .by_agent = (
          .runs
          | group_by(.agent_model)
          | map(
              ([.[] | .duration_seconds | select(. != null)] | sort) as $durations |
              ([.[] | .agent_duration_seconds | select(. != null)] | sort) as $agent_durations |
              {
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
                mean_duration_seconds: (if ($durations | length) == 0 then null else (($durations | add) / ($durations | length)) end),
                median_duration_seconds: (if ($durations | length) == 0 then null elif ($durations | length) % 2 == 1 then $durations[($durations | length) / 2 | floor] else (($durations[(($durations | length) / 2) - 1] + $durations[($durations | length) / 2]) / 2) end),
                mean_agent_duration_seconds: (if ($agent_durations | length) == 0 then null else (($agent_durations | add) / ($agent_durations | length)) end),
                median_agent_duration_seconds: (if ($agent_durations | length) == 0 then null elif ($agent_durations | length) % 2 == 1 then $agent_durations[($agent_durations | length) / 2 | floor] else (($agent_durations[(($agent_durations | length) / 2) - 1] + $agent_durations[($agent_durations | length) / 2]) / 2) end),
                known_cost_usd: ([.[].cost_usd // 0] | add // 0),
                cost_reported_jobs: ([.[] | select(.cost_usd != null)] | length)
              }
            )
        )
    '#

    let summary_file = ([$matrix_dir summary.json] | path join)
    let summary_json = (^jq --slurp --arg generated_at $generated_at $jq_filter ...$result_files)
    $summary_json | save --force $summary_file

    print $"summary: ($summary_file)"
    print ((open $summary_file).by_agent | select agent_model trials errors mean mean_duration_seconds median_duration_seconds known_cost_usd)
}
