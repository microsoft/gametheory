FROM python:3.12.11-slim-bookworm
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg unixodbc \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean \
    && mkdir /app/.build
COPY requirements.lock ./
RUN TMPDIR=/app/.build pip install --no-cache-dir --require-hashes -r requirements.lock
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
ENV PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
EXPOSE 8088
CMD ["uvicorn", "flood_lab.api:app", "--host", "0.0.0.0", "--port", "8088", "--no-access-log"]
