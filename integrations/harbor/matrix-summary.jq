def seconds:
  if . == null then null
  else (
    sub("Z$"; "")
    | sub("\\.[0-9]+$"; "")
    | . + "Z"
    | fromdateiso8601
  )
  end;

def scenario_name:
  if . == "002-postgres-rejecting-connectio" then
    "002-postgres-rejecting-connections"
  else .
  end;

def category_counts:
  map(select(. != null))
  | group_by(.)
  | map({category: .[0], count: length});

def failure_for($eval; $trial_id):
  if (.stats.n_errored_trials // 0) > 0 then
    if ($eval.value.exception_stats | tostring | ascii_downcase
        | test("timeout|timed out|deadline")) then
      {category: "timeout", message: null}
    else
      {category: "agent_error", message: null}
    end
  elif ($eval.value.metrics[0].mean // null) == 0 then
    ($failure_details[$trial_id] // {
      category: "uncategorized",
      message: null
    })
  else null
  end;

{
  schema_version: 2,
  generated_at: $generated_at,
  expected_trials: $expected_trials,
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
    ([$eval.value.reward_stats | .. | strings]
      + [$eval.value.exception_stats | .. | strings] | first) as $trial_id |
    (failure_for($eval; $trial_id)) as $failure |
    (.started_at | seconds) as $started |
    (.finished_at | seconds) as $finished |
    (.agent_execution.started_at | seconds) as $agent_started |
    (.agent_execution.finished_at | seconds) as $agent_finished |
    {
      job_id: .id,
      scenario: (($trial_id // "unknown") | split("__")[0] | scenario_name),
      agent_model: $eval.key,
      trials: (.stats.n_completed_trials // 0),
      errors: (.stats.n_errored_trials // 0),
      mean: ($eval.value.metrics[0].mean // null),
      failure_category: ($failure.category // null),
      failure_message: ($failure.message // null),
      input_tokens: .stats.n_input_tokens,
      cache_tokens: .stats.n_cache_tokens,
      output_tokens: .stats.n_output_tokens,
      cost_usd: .stats.cost_usd,
      duration_seconds: (
        if ($started != null and $finished != null)
        then $finished - $started else null end
      ),
      agent_duration_seconds: (
        if ($agent_started != null and $agent_finished != null)
        then $agent_finished - $agent_started else null end
      )
    }
  ]
}
| .failure_categories = ([.runs[].failure_category] | category_counts)
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
          failure_categories: ([.[].failure_category] | category_counts),
          input_tokens: ([.[].input_tokens // 0] | add // 0),
          cache_tokens: ([.[].cache_tokens // 0] | add // 0),
          output_tokens: ([.[].output_tokens // 0] | add // 0),
          mean_duration_seconds: (
            if ($durations | length) == 0 then null
            else (($durations | add) / ($durations | length)) end
          ),
          median_duration_seconds: (
            if ($durations | length) == 0 then null
            elif ($durations | length) % 2 == 1
            then $durations[($durations | length) / 2 | floor]
            else (($durations[(($durations | length) / 2) - 1]
              + $durations[($durations | length) / 2]) / 2) end
          ),
          mean_agent_duration_seconds: (
            if ($agent_durations | length) == 0 then null
            else (($agent_durations | add) / ($agent_durations | length)) end
          ),
          median_agent_duration_seconds: (
            if ($agent_durations | length) == 0 then null
            elif ($agent_durations | length) % 2 == 1
            then $agent_durations[($agent_durations | length) / 2 | floor]
            else (($agent_durations[(($agent_durations | length) / 2) - 1]
              + $agent_durations[($agent_durations | length) / 2]) / 2) end
          ),
          known_cost_usd: ([.[].cost_usd // 0] | add // 0),
          cost_reported_jobs: ([.[] | select(.cost_usd != null)] | length)
        }
      )
  )
| .by_scenario = (
    .runs
    | group_by(.scenario)
    | map({
        scenario: .[0].scenario,
        trials: ([.[].trials] | add // 0),
        errors: ([.[].errors] | add // 0),
        mean: (([.[] | select(.mean != null) | (.mean * .trials)] | add // 0)
          / ([.[] | select(.mean != null) | .trials] | add // 1)),
        failure_categories: ([.[].failure_category] | category_counts)
      })
  )
| .by_scenario_agent = (
    .runs
    | group_by([.scenario, .agent_model])
    | map(
        ([.[] | .duration_seconds | select(. != null)] | sort) as $durations |
        {
          scenario: .[0].scenario,
          agent_model: .[0].agent_model,
          trials: ([.[].trials] | add // 0),
          errors: ([.[].errors] | add // 0),
          mean: (([.[] | select(.mean != null) | (.mean * .trials)] | add // 0)
            / ([.[] | select(.mean != null) | .trials] | add // 1)),
          failure_categories: ([.[].failure_category] | category_counts),
          median_duration_seconds: (
            if ($durations | length) == 0 then null
            elif ($durations | length) % 2 == 1
            then $durations[($durations | length) / 2 | floor]
            else (($durations[(($durations | length) / 2) - 1]
              + $durations[($durations | length) / 2]) / 2) end
          ),
          known_cost_usd: ([.[].cost_usd // 0] | add // 0),
          cost_reported_jobs: ([.[] | select(.cost_usd != null)] | length)
        }
      )
  )
