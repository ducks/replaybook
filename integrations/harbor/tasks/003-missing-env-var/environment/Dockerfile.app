FROM python:3.12-alpine

WORKDIR /app
COPY app.py run.sh app-broken.env /app/
COPY app-broken.env /app/.env
RUN chmod 0755 /app/run.sh

CMD ["sh", "/app/run.sh"]
