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

    let script_dir = ($env.FILE_PWD | path expand)
    let summary_filter = ([$script_dir matrix-summary.jq] | path join)
    let generated_at = (date now | date to-timezone "+0000" | format date "%Y-%m-%dT%H:%M:%SZ")
    let expected_trials = (
        ^jq --slurp '[.[].stats | (.n_completed_trials // 0) + (.n_errored_trials // 0) + (.n_pending_trials // 0) + (.n_cancelled_trials // 0)] | add // 0' ...$result_files
        | into int
    )
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

    let summary_file = ([$matrix_dir summary.json] | path join)
    let summary_json = (
        ^jq --slurp
            --arg generated_at $generated_at
            --argjson expected_trials ($expected_trials | into string)
            --argjson failure_details ($failure_details | to json)
            --from-file $summary_filter
            ...$result_files
    )
    $summary_json | save --force $summary_file

    let summary = (open $summary_file)
    print $"summary: ($summary_file)"
    print ($summary.by_agent | select agent_model trials errors mean mean_duration_seconds median_duration_seconds known_cost_usd)
    print ""
    print ($summary.by_scenario_agent | select scenario agent_model trials errors mean median_duration_seconds known_cost_usd)
    if not ($summary.failure_categories | is-empty) {
        print ""
        print ($summary.failure_categories | select category count)
    }
}
