FROM python:3.12-alpine

WORKDIR /app
COPY app.py start.sh ./
RUN chmod 0755 /app/start.sh

CMD ["/app/start.sh"]
