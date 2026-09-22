# Development

## Prerequisites

- Node 22+ and npm.
- Python 3.12, ODBC Driver 18, and a supported SQL Server/Azure SQL test instance.
- A preconfigured Entra tenant and SPA/API registrations for interactive sign-in.
- Blob Storage or the included local Azurite tool.
- For real planning: an authorized Foundry project/model and managed Scheduler
  endpoint, or the local Scheduler emulator with a configured model.

SQL Server containers require a supported host, such as Linux x64. Do not treat
SQLite as an integration substitute. If your development host cannot run SQL
Server, use a specifically designated test database or the Linux CI job.

## Install and configure

From the repository root:

```bash
npm ci
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install --require-hashes -r backend/requirements-dev.lock
backend/.venv/bin/pip install --no-deps -e backend
cp .env.example .env
```

Windows uses the corresponding `Scripts` executables. Settings read the root
`.env`; run backend commands from the repository root.

`requirements.lock` and `requirements-dev.lock` pin runtime and development
dependencies with distribution hashes and cross-platform markers. They are
generated with uv's universal requirements compiler; neither embeds a private
package index. Both pip and uv can install them into an existing environment.

For dependency updates, use uv and the Python 3.12 target:

```bash
uv pip compile --universal --python-version 3.12 --generate-hashes \
  backend/pyproject.toml -o backend/requirements.lock
uv pip compile --universal --python-version 3.12 --generate-hashes --extra dev \
  -c backend/requirements.lock backend/pyproject.toml -o backend/requirements-dev.lock
```

### Entra

Create/configure two single-tenant registrations:

1. An API registration exposing a delegated `access_as_user` scope and configured
   to issue v2 access tokens. Set its actual token audience in `GT_API_AUDIENCE`.
2. A SPA registration with `http://localhost:5173` as a SPA redirect URI (also add
   the actual production origin when deploying). Grant/consent to the API scope.

Set `GT_TENANT_ID`, `GT_SPA_CLIENT_ID`, and `GT_API_SCOPE` (full scope URI).
The redirect URI is the current browser origin. Use the exact registered origin;
`localhost` and `127.0.0.1` are different. If using Vite at `127.0.0.1:5173`, register
that exact origin as well. Sign-in does not automatically create an administrator.

### SQL

Set `GT_SQL_URL` to a dedicated **application** database. For example, a local test
instance can use:

```text
mssql+pyodbc://USER:PASSWORD@127.0.0.1:1433/gametheory?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes
```

URL-encode credentials; never commit them. `TrustServerCertificate=yes` is only
for an explicitly trusted local test instance. Azure connections must validate
certificates and should use managed identity. The SQLAlchemy query key
`authentication=ActiveDirectoryMsi` is lower-case intentionally. For a user-assigned
identity, also supply its client ID as `UID`.

Run migrations with a separate migration/operator identity, then bootstrap:

```bash
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
backend/.venv/bin/gametheory bootstrap \
  --tenant TENANT_UUID --object-id ADMIN_OBJECT_UUID \
  --organization-name "Your organization"
```

The tenant must match configuration. Bootstrap creates standard environment labels
and an explicit organization administrator; it does not activate external systems.

### Blob Storage

For Azure, set `GT_BLOB_URL` and `GT_BLOB_CONTAINER` and grant the API identity Blob
data access to that private container. Create the container as an operator.

For local development, run `npx azurite-blob --blobHost 127.0.0.1`. Set
`GT_BLOB_CONNECTION_STRING` to Azurite's documented development connection string
with an explicit loopback `BlobEndpoint`. The Python SDK does not use the abbreviated
`.NET` `UseDevelopmentStorage=true` setting here. Never use shared-key connection
strings for cloud storage. The test suite starts a separate loopback Azurite process
with an ephemeral account, so it does not need your development storage credentials.

Failed uploads remain staged. Inspect before deleting abandoned staging data:

```bash
backend/.venv/bin/gametheory cleanup-staged
backend/.venv/bin/gametheory cleanup-staged --apply --older-than-hours 24
```

Use an operator identity. Storage failures are reported, not swallowed; rerun to
resume pending cleanup. Drain abandoned upload processes before final cleanup.
Storage soft-delete retention/backups still apply; this is not a compliance erasure
or universal deletion guarantee.

### Planning

Set `GT_SCHEDULER_ENDPOINT`, `GT_SCHEDULER_TASKHUB`,
`GT_FOUNDRY_PROJECT_ENDPOINT`, `GT_MODEL_DEPLOYMENT`, and
`GT_PLANNING_ENABLED=true`. Use `DefaultAzureCredential` with the intended local
developer identity or deployed managed identity. The identity needs native model
access and **Durable Task Data Contributor** on the selected task hub.

The local scheduler uses `GT_SCHEDULER_ENDPOINT=http://127.0.0.1:8080`,
`GT_SCHEDULER_EMULATOR=true`, and its configured/default task hub. Emulator mode
accepts only explicit local addresses, never arbitrary unauthenticated hosts.
No infrastructure or model deployment is created by starting the worker.

## Run

```bash
backend/.venv/bin/uvicorn gametheory.api:app --host 127.0.0.1 --port 8000 --reload
npm run dev
# In another terminal, only when planning is configured:
backend/.venv/bin/gametheory-worker
```

Vite proxies `/api` to port 8000. Without Entra settings the browser shows setup
requirements, not a sign-in bypass. Without storage/model settings it reports
unavailable capabilities, not simulated successes.

## Checks

```bash
backend/.venv/bin/ruff check backend/src backend/tests backend/migrations
backend/.venv/bin/mypy --config-file backend/pyproject.toml backend/src
backend/.venv/bin/pytest -q backend/tests
backend/.venv/bin/gametheory openapi
npm run contracts
npm test
npm run build
npm run test:e2e
az bicep build --file infra/main.bicep --stdout
```

Install the Playwright Chromium browser with `npx playwright install chromium` if
it is missing. Real SQL tests require `GT_TEST_SQL_URL` naming a disposable database
whose name starts with `gametheory_test`. They apply migrations and write isolated
test records; do not point them at organization data. Scheduler crash/restart tests
also require `GT_TEST_SCHEDULER_ENDPOINT`. Missing services produce explicit skips.

Browser interaction tests use a clearly labeled Vite **test-mode-only** harness and
network fixtures. That entry is not built into the production bundle, cannot
authenticate to the API, and is not evidence of SQL/Entra/model integration.
The real Blob test starts Azurite. The CI job supplies real SQL Server and Scheduler
emulator services and runs the same application worker with a test-only model fixture.
Live Entra sign-in, managed identity/RBAC, and Foundry inference require separate
authorized deployment validation.
