FROM python:3.12-alpine

WORKDIR /app
COPY app.py /app/app.py
COPY retry-broken.conf /app/retry.conf

CMD ["python", "/app/app.py"]
