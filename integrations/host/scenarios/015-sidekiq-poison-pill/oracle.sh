#!/usr/bin/env bash
set -euo pipefail

jobs=/var/lib/checkout/current/jobs.rb
ruby - "$jobs" <<'RUBY'
path = ARGV.fetch(0)
text = File.read(path)
text = text.sub(
  "    loop { sleep 60 } if payload == \"poison\"\n",
  <<~'REPLACEMENT'.lines.map { |line| "    #{line}" }.join
    if payload == "poison"
      connection.exec_params(
        "INSERT INTO quarantined_jobs (job_id, reason) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        [job_id, "invalid checkout payload"]
      )
      return
    end
  REPLACEMENT
)
File.write(path, text)
RUBY

mapfile -t poison_ids < <(psql --host 127.0.0.1 --username replaybook --dbname replaybook --tuples-only --no-align \
  --command="SELECT job_id FROM job_attempts WHERE job_id LIKE 'poison-%' AND job_id NOT IN (SELECT job_id FROM completed_jobs) AND job_id NOT IN (SELECT job_id FROM quarantined_jobs)")
for job_id in "${poison_ids[@]}"; do
  psql --host 127.0.0.1 --username replaybook --dbname replaybook \
    --command="INSERT INTO quarantined_jobs (job_id, reason) VALUES ('$job_id', 'invalid checkout payload') ON CONFLICT DO NOTHING" >/dev/null
done
systemctl restart checkout-sidekiq.service
