# frozen_string_literal: true

require "pg"
require "socket"
require_relative "jobs"

def response(socket, status, body)
  reason = status == 200 ? "OK" : status == 202 ? "Accepted" : "Not Found"
  socket.write(
    "HTTP/1.1 #{status} #{reason}\r\n" \
    "Content-Type: text/plain\r\n" \
    "Content-Length: #{body.bytesize}\r\n" \
    "Connection: close\r\n\r\n" \
    "#{body}"
  )
end

def completed?(job_id)
  connection = PG.connect(
    host: "127.0.0.1",
    dbname: "replaybook",
    user: "replaybook"
  )
  result = connection.exec_params(
    "SELECT 1 FROM completed_jobs WHERE job_id = $1",
    [job_id]
  )
  result.ntuples == 1
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
  else
    if method == "POST" && path&.start_with?("/jobs/")
      job_id = path.delete_prefix("/jobs/")
      CheckoutConfirmationJob.perform_async(job_id)
      response(socket, 202, "queued")
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
