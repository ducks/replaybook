FROM python:3.12-alpine

WORKDIR /app
COPY app.py /app/app.py
ARG RELEASE_FILE=release-good.env
COPY ${RELEASE_FILE} /app/release.env

CMD ["python", "/app/app.py"]
