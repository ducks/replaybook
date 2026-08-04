FROM python:3.12-alpine

WORKDIR /app
COPY app.py run.sh cache.conf ./
RUN chmod 0755 /app/run.sh

CMD ["/app/run.sh"]
