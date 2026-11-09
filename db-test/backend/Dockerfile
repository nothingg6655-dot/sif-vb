FROM node:24-bookworm-slim AS node-build
WORKDIR /app/db-test/backend
COPY db-test/backend/package*.json ./
RUN npm ci --include=dev
COPY db-test/backend/tsconfig.json ./
COPY db-test/backend/src ./src
RUN npm run build && npm prune --omit=dev

FROM node:24-bookworm-slim
ENV NODE_ENV=production HOST=0.0.0.0 PORT=10000 \
    TZ=Asia/Kolkata APP_TIMEZONE=Asia/Kolkata \
    VIRTUAL_ENV=/opt/venv PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    SCHEDULER_PYTHON=/opt/venv/bin/python \
    FACTSHEET_DATA_DIR=/app/db-test/data/factsheets FACTSHEET_MAX_WORKERS=1
ENV PATH="/opt/venv/bin:${PATH}"
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv ca-certificates tini \
    && python3 -m venv /opt/venv \
    && rm -rf /var/lib/apt/lists/*
COPY db-test/ingestion/requirements.lock.txt /tmp/nav-requirements.txt
COPY db-test/backend/requirements-scheduler.lock.txt /tmp/factsheet-requirements.txt
RUN pip install --no-cache-dir --retries 5 --timeout 120 -r /tmp/nav-requirements.txt -r /tmp/factsheet-requirements.txt
RUN PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000 python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /tmp/*
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/db-test
COPY db-test/ingestion ./ingestion
COPY db-test/database ./database
COPY db-test/factsheetDataIngestion ./factsheetDataIngestion
COPY db-test/scripts/run_server_job.py ./scripts/run_server_job.py
COPY db-test/backend/certs ./backend/certs
COPY --from=node-build /app/db-test/backend/node_modules ./backend/node_modules
COPY --from=node-build /app/db-test/backend/dist ./backend/dist
COPY --from=node-build /app/db-test/backend/package.json ./backend/package.json
RUN mkdir -p /app/db-test/data/factsheets /app/db-test/logs \
    && chown -R node:node /app/db-test/data /app/db-test/logs
USER node
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/db-test/backend
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "dist/server.js"]
