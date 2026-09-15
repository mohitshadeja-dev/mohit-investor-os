FROM python:3.11-slim

WORKDIR /app

COPY mohit_investor_os_app.zip /app/app.zip

RUN apt-get update && apt-get install -y unzip && \
    unzip /app/app.zip -d /app && \
    pip install --no-cache-dir -r /app/requirements.txt

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
