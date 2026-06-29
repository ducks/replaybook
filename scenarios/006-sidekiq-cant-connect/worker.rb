require "sidekiq"

Sidekiq.configure_server do |config|
  config.redis = { url: ENV.fetch("REDIS_URL", "redis://redis:6379/0") }
end

class EmailWorker
  include Sidekiq::Job

  def perform(user_id)
    puts "Sending email to user #{user_id}"
  end
end
