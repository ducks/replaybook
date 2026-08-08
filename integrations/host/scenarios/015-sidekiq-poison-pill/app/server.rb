# frozen_string_literal: true

require "pg"
require "socket"
require_relative "jobs"

def response(socket, status, body)
  reasons = { 200 => "OK", 202 => "Accepted", 404 => "Not Found" }
  socket.write(
    "HTTP/1.1 #{status} #{reasons.fetch(status)}\r\n" \
    "Content-Type: text/plain\r\n" \
    "Content-Length: #{body.bytesize}\r\n" \
    "Connection: close\r\n\r\n#{body}"
  )
end

def job_state(job_id)
  connection = PG.connect(host: "127.0.0.1", dbname: "replaybook", user: "replaybook")
  return "completed" if connection.exec_params("SELECT 1 FROM completed_jobs WHERE job_id = $1", [job_id]).ntuples == 1
  return "quarantined" if connection.exec_params("SELECT 1 FROM quarantined_jobs WHERE job_id = $1", [job_id]).ntuples == 1

  "pending"
ensure
  connection&.close
end

server = TCPServer.new("127.0.0.1", 3000)

loop do
  socket = server.accept
  method, path = socket.gets.to_s.split(" ", 3)
  while (line = socket.gets)
    break if line == "\r\n"
  end

  if method == "GET" && path == "/health"
    response(socket, 200, "ok")
  elsif method == "POST" && path&.start_with?("/jobs/")
    parts = path.delete_prefix("/jobs/").split("/", 2)
    CheckoutConfirmationJob.perform_async(parts[0], parts[1] == "poison" ? "poison" : "valid")
    response(socket, 202, "queued")
  elsif method == "GET" && path&.start_with?("/jobs/")
    job_path = path.delete_prefix("/jobs/")
    if job_path.end_with?("/attempts")
      job_id = job_path.delete_suffix("/attempts")
      connection = PG.connect(host: "127.0.0.1", dbname: "replaybook", user: "replaybook")
      result = connection.exec_params("SELECT attempt_count FROM job_attempts WHERE job_id = $1", [job_id])
      response(socket, result.ntuples == 1 ? 200 : 404, result.ntuples == 1 ? result[0]["attempt_count"] : "missing")
      connection.close
    else
      state = job_state(job_path)
      response(socket, state == "pending" ? 404 : 200, state)
    end
  else
    response(socket, 404, "not found")
  end
rescue StandardError => error
  warn "#{error.class}: #{error.message}"
  response(socket, 404, "error") if socket
ensure
  socket&.close
end
