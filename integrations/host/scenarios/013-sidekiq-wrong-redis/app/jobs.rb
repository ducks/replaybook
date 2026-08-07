# frozen_string_literal: true

require "pg"
require "sidekiq"

redis_url = ENV.fetch("REDIS_URL")

Sidekiq.configure_client do |config|
  config.redis = { url: redis_url }
end

Sidekiq.configure_server do |config|
  config.redis = { url: redis_url }
end

class CheckoutConfirmationJob
  include Sidekiq::Job

  def perform(job_id)
    connection = PG.connect(
      host: "127.0.0.1",
      dbname: "replaybook",
      user: "replaybook"
    )
    connection.exec_params(
      "INSERT INTO completed_jobs (job_id) VALUES ($1) ON CONFLICT DO NOTHING",
      [job_id]
    )
  ensure
    connection&.close
  end
end
