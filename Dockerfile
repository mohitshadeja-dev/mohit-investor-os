FROM python:3.11-slim

WORKDIR /app

COPY mohit_investor_os_app.zip /app/app.zip

RUN apt-get update && apt-get install -y unzip && \
    unzip /app/app.zip -d /app && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    mv /usr/local/bin/uvicorn /usr/local/bin/uvicorn-real && \
    printf '%s\n' '#!/bin/sh' \
      'set -- "$@"' \
      'out=""' \
      'while [ "$#" -gt 0 ]; do' \
      '  if [ "$1" = "\$PORT" ]; then set -- 8000 "${@:2}"; fi' \
      '  out="$out \"$1\""' \
      '  shift' \
      'done' \
      'eval exec /usr/local/bin/uvicorn-real $out' \
      > /usr/local/bin/uvicorn && chmod +x /usr/local/bin/uvicorn

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
