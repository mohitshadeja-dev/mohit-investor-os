FROM python:3.11-slim

WORKDIR /app

COPY mohit_investor_os_app.zip /app/app.zip
COPY index-production.html /app/index-production.html

RUN apt-get update && apt-get install -y unzip && \
    unzip /app/app.zip -d /app && \
    cp /app/index-production.html /app/app/static/index.html && \
    pip install --no-cache-dir -r /app/requirements.txt

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
