FROM python:3.12-alpine

WORKDIR /app
COPY app.py /app/app.py
COPY auth-broken.env /app/auth.env

CMD ["python", "/app/app.py"]
