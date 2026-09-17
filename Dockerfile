FROM python:3.11-slim
WORKDIR /app
COPY mohit_investor_os_app.zip /app/app.zip
COPY index-production.html /app/index-production.html
COPY research-dashboard.html /app/research-dashboard.html
COPY store-production.py /app/store-production.py
COPY main-production.py /app/main-production.py
COPY strategy-lab.py /app/strategy-lab.py
COPY strategy-lab-patched.py /app/strategy-lab-patched.py
COPY strategy-lab-router.py /app/strategy-lab-router.py
COPY same-day-sr.py /app/same-day-sr.py
COPY weekly-wma-gann.py /app/weekly-wma-gann.py
COPY weekly-route-patch.py /app/weekly-route-patch.py
COPY lab-upgrade.js /app/lab-upgrade.js
COPY presets-7750.js /app/presets-7750.js
COPY weekly-wma-gann.js /app/weekly-wma-gann.js
COPY weekly-reverse-only.js /app/weekly-reverse-only.js
COPY options_backtest.py /app/options_backtest.py
COPY stockmock-worklist.js /app/stockmock-worklist.js
COPY live-execution.py /app/live-execution.py
COPY live-trading-window.js /app/live-trading-window.js
COPY journal-diary.js /app/journal-diary.js
COPY master-framework.js /app/master-framework.js
COPY master-analysis.py /app/master-analysis.py
COPY live-signal-7575.py /app/live-signal-7575.py
COPY inject-lab.py /app/inject-lab.py
RUN apt-get update && apt-get install -y unzip && \
    unzip /app/app.zip -d /app && \
    cp /app/index-production.html /app/app/static/index.html && \
    cp /app/research-dashboard.html /app/app/static/research-dashboard.html && \
    cp /app/store-production.py /app/app/store.py && \
    cp /app/main-production.py /app/app/main.py && \
    cp /app/strategy-lab.py /app/app/strategy_lab_base.py && \
    cp /app/strategy-lab-patched.py /app/app/strategy_lab_patched_impl.py && \
    cp /app/strategy-lab-router.py /app/app/strategy_lab.py && \
    cp /app/same-day-sr.py /app/app/same_day_sr.py && \
    cp /app/weekly-wma-gann.py /app/app/weekly_wma_gann.py && \
    python /app/weekly-route-patch.py && \
    cp /app/lab-upgrade.js /app/app/static/lab-upgrade.js && \
    cp /app/presets-7750.js /app/app/static/presets-7750.js && \
    cp /app/weekly-wma-gann.js /app/app/static/weekly-wma-gann.js && \
    cp /app/weekly-reverse-only.js /app/app/static/weekly-reverse-only.js && \
    cp /app/options_backtest.py /app/app/options_backtest.py && \
    cp /app/stockmock-worklist.js /app/app/static/stockmock-worklist.js && \
    cp /app/live-execution.py /app/app/live_execution.py && \
    cp /app/live-trading-window.js /app/app/static/live-trading-window.js && \
    cp /app/journal-diary.js /app/app/static/journal-diary.js && \
    cp /app/master-framework.js /app/app/static/master-framework.js && \
    cp /app/master-analysis.py /app/app/master_analysis.py && \
    cp /app/live-signal-7575.py /app/app/live_signal_7575.py && \
    python /app/inject-lab.py && \
    pip install --no-cache-dir -r /app/requirements.txt yfinance && \
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
