FROM python:3.11-slim

WORKDIR /app

COPY mohit_investor_os_app.zip /app/app.zip
COPY index-production.html /app/index-production.html
COPY store-production.py /app/store-production.py

RUN apt-get update && apt-get install -y unzip && \
    unzip /app/app.zip -d /app && \
    cp /app/index-production.html /app/app/static/index.html && \
    cp /app/store-production.py /app/app/store.py && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    mv /usr/local/bin/uvicorn /usr/local/bin/uvicorn-real && \
    printf '%s\n' '#!/bin/sh' \
      'args=""' \
      'for arg in "$@"; do' \
      '  if [ "$arg" = "\$PORT" ]; then arg="8000"; fi' \
      '  args="$args \"$arg\""' \
      'done' \
      'eval exec /usr/local/bin/uvicorn-real $args' \
      > /usr/local/bin/uvicorn && chmod +x /usr/local/bin/uvicorn

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
