# frozen_string_literal: true

require "pg"
require "securerandom"
require "socket"
require_relative "jobs"

def response(socket, status, body)
  reasons = { 200 => "OK", 202 => "Accepted", 404 => "Not Found" }
  socket.write(
    "HTTP/1.1 #{status} #{reasons.fetch(status)}\r\n" \
    "Content-Type: text/plain\r\n" \
    "Content-Length: #{body.bytesize}\r\n" \
    "Connection: close\r\n\r\n" \
    "#{body}"
  )
end

def database
  PG.connect(host: "127.0.0.1", dbname: "replaybook", user: "replaybook")
end

def completed?(job_id)
  connection = database
  result = connection.exec_params(
    "SELECT 1 FROM checkout_confirmations WHERE job_id = $1",
    [job_id]
  )
  result.ntuples == 1
ensure
  connection&.close
end

def attempts(job_id)
  connection = database
  result = connection.exec_params(
    "SELECT attempt_count FROM job_attempts WHERE job_id = $1",
    [job_id]
  )
  result.ntuples == 1 ? result[0]["attempt_count"] : "0"
ensure
  connection&.close
end

def migration_applied?
  connection = database
  version = connection.exec_params(
    "SELECT 1 FROM schema_migrations WHERE version = $1",
    ["202608070001"]
  )
  column = connection.exec(<<~SQL)
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'checkout_confirmations'
      AND column_name = 'delivery_state'
      AND is_nullable = 'NO'
  SQL
  version.ntuples == 1 && column.ntuples == 1
ensure
  connection&.close
end

server = TCPServer.new("127.0.0.1", 3000)

loop do
  socket = server.accept
  request_line = socket.gets.to_s
  method, path = request_line.split(" ", 3)
  while (line = socket.gets)
    break if line == "\r\n"
  end

  case [method, path]
  when ["GET", "/health"]
    response(socket, 200, "ok")
  when ["GET", "/deployment/migration"]
    migration_applied? ? response(socket, 200, "applied") : response(socket, 404, "missing")
  else
    if method == "POST" && path&.start_with?("/jobs/")
      job_id = path.delete_prefix("/jobs/")
      CheckoutConfirmationJob.perform_async(job_id, SecureRandom.hex(16))
      response(socket, 202, "queued")
    elsif method == "GET" && path&.start_with?("/jobs/") && path&.end_with?("/attempts")
      job_id = path.delete_prefix("/jobs/").delete_suffix("/attempts")
      response(socket, 200, attempts(job_id))
    elsif method == "GET" && path&.start_with?("/jobs/")
      job_id = path.delete_prefix("/jobs/")
      completed?(job_id) ? response(socket, 200, "completed") : response(socket, 404, "pending")
    else
      response(socket, 404, "not found")
    end
  end
rescue StandardError => error
  warn "#{error.class}: #{error.message}"
  response(socket, 404, "error") if socket
ensure
  socket&.close
end
