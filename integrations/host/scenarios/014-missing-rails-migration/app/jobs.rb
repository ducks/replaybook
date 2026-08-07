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

  sidekiq_options retry: 100
  sidekiq_retry_in { 10 }

  def perform(job_id, confirmation_code)
    connection = PG.connect(
      host: "127.0.0.1",
      dbname: "replaybook",
      user: "replaybook"
    )
    connection.exec_params(
      <<~SQL,
        INSERT INTO job_attempts (job_id, attempt_count)
        VALUES ($1, 1)
        ON CONFLICT (job_id) DO UPDATE
        SET attempt_count = job_attempts.attempt_count + 1
      SQL
      [job_id]
    )
    connection.exec_params(
      <<~SQL,
        INSERT INTO checkout_confirmations
          (job_id, confirmation_code, delivery_state)
        VALUES ($1, $2, 'confirmed')
        ON CONFLICT (job_id) DO NOTHING
      SQL
      [job_id, confirmation_code]
    )
  ensure
    connection&.close
  end
end
