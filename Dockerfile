FROM node:22-bookworm-slim AS web
WORKDIR /build
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
RUN npm ci --no-audit --no-fund
COPY apps/web apps/web
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates unixodbc \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -o /tmp/msprod.deb \
    && dpkg -i /tmp/msprod.deb && rm /tmp/msprod.deb \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean
WORKDIR /app
COPY backend/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY backend/pyproject.toml backend/alembic.ini backend/
COPY backend/src backend/src
COPY backend/migrations backend/migrations
RUN pip install --no-cache-dir --no-deps ./backend && useradd --uid 10001 --create-home app
USER app

FROM runtime AS validation
USER root
COPY backend/requirements-dev.lock /tmp/requirements-dev.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-dev.lock
COPY backend/tests backend/tests
USER app
CMD ["pytest", "-q", "backend/tests"]

FROM runtime AS worker
CMD ["gametheory-worker"]

FROM runtime AS api
COPY --from=web /build/apps/web/dist /app/apps/web/dist
EXPOSE 8000
CMD ["uvicorn", "gametheory.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
