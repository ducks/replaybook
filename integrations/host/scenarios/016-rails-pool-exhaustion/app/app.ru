# frozen_string_literal: true

require "active_record"
require "json"

ActiveRecord::Base.establish_connection(
  adapter: "postgresql",
  host: "127.0.0.1",
  database: "replaybook",
  username: "replaybook",
  pool: Integer(ENV.fetch("DB_POOL", "1")),
  checkout_timeout: 0.25
)

class CheckoutApp
  def call(env)
    method = env.fetch("REQUEST_METHOD")
    path = env.fetch("PATH_INFO")

    return text(200, "ok") if method == "GET" && path == "/health"
    return text(200, ActiveRecord::Base.connection_pool.size.to_s) if method == "GET" && path == "/pool"

    if method == "POST" && path.start_with?("/checkouts/")
      checkout_id = path.delete_prefix("/checkouts/")
      ActiveRecord::Base.connection_pool.with_connection do |connection|
        connection.execute("SELECT pg_sleep(1)")
        quoted = connection.quote(checkout_id)
        connection.execute("INSERT INTO completed_checkouts (checkout_id) VALUES (#{quoted}) ON CONFLICT DO NOTHING")
      end
      return text(200, "completed")
    end

    if method == "GET" && path.start_with?("/checkouts/")
      checkout_id = path.delete_prefix("/checkouts/")
      completed = ActiveRecord::Base.connection_pool.with_connection do |connection|
        quoted = connection.quote(checkout_id)
        connection.select_value("SELECT 1 FROM completed_checkouts WHERE checkout_id = #{quoted}") == 1
      end
      return text(completed ? 200 : 404, completed ? "completed" : "missing")
    end

    text(404, "not found")
  rescue ActiveRecord::ConnectionTimeoutError => error
    warn "#{error.class}: #{error.message}"
    text(503, "database pool exhausted")
  end

  private

  def text(status, body)
    [status, { "content-type" => "text/plain", "content-length" => body.bytesize.to_s }, [body]]
  end
end

run CheckoutApp.new
