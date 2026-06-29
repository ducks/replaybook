FROM ruby:3.3-alpine
RUN apk add --no-cache build-base tmux
WORKDIR /app
COPY Gemfile .
RUN bundle install
COPY worker.rb .
CMD ["bundle", "exec", "sidekiq", "-r", "./worker.rb"]
