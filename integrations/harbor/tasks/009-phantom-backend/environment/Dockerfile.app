FROM python:3.12-alpine

WORKDIR /app
COPY app.py start-app.sh ./
RUN chmod 0755 /app/start-app.sh

CMD ["/app/start-app.sh"]
