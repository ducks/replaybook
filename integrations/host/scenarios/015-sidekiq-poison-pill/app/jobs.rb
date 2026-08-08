# frozen_string_literal: true

require "pg"
require "sidekiq"

redis_url = ENV.fetch("REDIS_URL")

Sidekiq.configure_client { |config| config.redis = { url: redis_url } }
Sidekiq.configure_server { |config| config.redis = { url: redis_url } }

class CheckoutConfirmationJob
  include Sidekiq::Job
  sidekiq_options retry: false

  def perform(job_id, payload = "valid")
    connection = PG.connect(host: "127.0.0.1", dbname: "replaybook", user: "replaybook")
    connection.exec_params(
      "INSERT INTO job_attempts (job_id) VALUES ($1) ON CONFLICT (job_id) DO UPDATE SET attempt_count = job_attempts.attempt_count + 1",
      [job_id]
    )

    loop { sleep 60 } if payload == "poison"

    connection.exec_params(
      "INSERT INTO completed_jobs (job_id) VALUES ($1) ON CONFLICT DO NOTHING",
      [job_id]
    )
  ensure
    connection&.close
  end
end
