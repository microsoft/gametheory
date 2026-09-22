# Independent flood response lab

**EXERCISE ONLY. Synthetic people, places and operational records.**

This is an independent Python 3.12 / FastAPI / SQL Server operational system and
separately built React/TypeScript application. It neither imports Game Theory nor
uses its database, auth registration, npm workspace or Docker images. There is
**no scenario installer, Game Theory API/SQL seed, execution approval or Graph
sender**. Users assemble preparation through Game Theory's ordinary UI.

## What is implemented

- Dedicated `flood` schema and Alembic migrations; a checked database identity
  marker refuses accidental use of another application's database.
- Entra v2 signature, issuer, tenant, audience, expiry, scope/application-role
  validation; separate expiring trusted actor/run grants.
- Bounded request/run/event reads, service-only request creation, participant
  acknowledgement and quantity allocation, opaque strong ETags.
- Transaction-owned SQL Server run locks, scoped unique idempotency receipts,
  durable UTC events and atomic mutation/receipt/evidence commits.
- Narrow allowlisted occupancy procedures and distinct API, injector, observer
  and operator database roles. SQL grants bind the database principal SID as
  well as its ID, preventing a recreated principal from inheriting old grants.
- Operator preview/apply migrations, deterministic seed manifests, grant
  management, occupancy injection and conditional per-record recovery.
- Real MSAL sign-in and an accessible standalone operations UI. Unknown
  mutations retain their exact inputs/key in an account-scoped session journal.
- Reproducible fictional assets and separate SQL/REST/Graph **description**
  catalogs. Graph descriptions and fixed templates are not implementations.

## Runtime prerequisites

Use Python **3.12**, Node **24** (Node 22.12+ also supports this Vite version),
ODBC Driver **18** for SQL Server, and a **dedicated** SQL Server 2019+/Azure SQL
database. There is no SQLite mode. The database name must match the configured
URL exactly and begin with `flood_lab_`. Opaque `odbc_connect` URLs are rejected.

Local SQL Server containers are supported here only on **Linux x86-64**. On an
Apple Silicon/macOS host, select an explicitly approved supported remote test
host instead; do not substitute SQLite or claim emulation establishes SQL
support. No cloud setup, firewall changes or permission provisioning is automated.

Commands below run from `exercises/flood-response/`. They do not use root tooling.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
npm --prefix ui ci
export PYTHONPATH="$PWD/src"
```

For a runtime-only source checkout/container, install `requirements.lock`
instead. The source checkout (including `migrations/`) is the supported CLI
distribution; the independent Dockerfile carries those same files. Do not
install this package into Game Theory's environment.

## Dedicated local SQL target

On an approved **Linux x86-64** host, choose a new local-only admin secret and
loopback port, then explicitly accept the SQL Server Developer EULA:

```bash
export FLOOD_LAB_ACCEPT_SQL_EULA=Y
export FLOOD_LAB_SQL_PORT=14339
# Set FLOOD_LAB_SQL_ADMIN_PASSWORD through your local secret mechanism; no default exists.
docker compose up -d sql
docker compose ps
```

Use your DBA tool or `sqlcmd` to create **new dedicated databases**, for example
`flood_lab_local` and, separately, `flood_lab_test_local`. Never point this
package at the Game Theory application database or an existing business DB.
Migrations do not create/drop databases and refuse initialization when unrelated
user tables are present. Preview/apply may create only the lab's schema/tables
inside an already selected database.

A database owner performs the initial migration with a separately configured
migration/operator identity. Then provision separate database principals and add
them only to their intended role:

- `flood_api`: the FastAPI process. Reads grants/shelters/runs; writes operational
  requests and allocations; inserts immutable receipts/events. Cannot change
  shelter occupancy, run grants, seed runs or recovery evidence.
- `flood_injector`: execute `flood.ReadOccupancy` / `flood.UpdateOccupancy` only.
- `flood_observer`: execute `flood.ReadOccupancy` only.
- `flood_operator`: seed, set trusted run grants and conditionally recover records.
  It cannot update/delete receipts, events or recovery evidence. Initial
  migrations still require explicit DDL authority, not merely this role.

Do not give the API `db_owner`, operator or injector membership. Participants
receive no SQL credentials. Use separate identities in environment variables;
the operator CLI never falls back to the API URL. Azure identity creation and
role assignments are a later authorized live gate, not a script in this lab.

Configure the process environment using `.env.example` as the field reference.
The API does **not** automatically read an `.env` file. URL structure:

```text
mssql+pyodbc://<separately-provisioned-principal>:<URL-encoded-secret>@<approved-host>:<port>/flood_lab_<selected-name>?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no
```

Prefer validated certificates. For a deliberately local disposable SQL
container only, `TrustServerCertificate=yes` may be explicitly selected.
Never print or commit URLs containing credentials.

```bash
python -m flood_lab.cli migrate
python -m flood_lab.cli migrate --apply
python -m flood_lab.cli seed --run-key riverwatch-001
python -m flood_lab.cli seed --run-key riverwatch-001 --apply
```

Use `.venv/bin/python` in place of `python` unless the venv is activated. Preview
is the default for every mutating CLI command. Save the output manifest, run ID,
seed owner operation and manifest hash as operator evidence. The same run key
and profile produce the same IDs and versions. Re-running seed never overwrites
records, resurrects recovered runs or changes another operator's run. To run a
new exercise, use a **new** run key.

## Entra authentication and trusted grants

Use explicitly selected, separate API and SPA registrations in the lab's tenant.
No registrations or consent changes are made by this repository.

1. Configure the API to accept v2 access tokens for its exact audience. Expose
   delegated scope `FloodLab.Access` (or explicitly configure a different scope).
2. For a separately authorized service, expose/assign application role
   `FloodLab.Service` (or the configured equivalent). Application access tokens
   must include `idtyp=app`; configure the Entra optional claim if necessary.
   A service token is not a participant token.
3. Configure the SPA's exact redirect origin/path and grant it only the selected
   delegated API scope. It does not request Graph permissions.
4. Set `FLOOD_LAB_ENTRA_TENANT_ID`, `FLOOD_LAB_ENTRA_AUDIENCE`,
   `FLOOD_LAB_ENTRA_SCOPE`, `FLOOD_LAB_ENTRA_SERVICE_ROLE` and an explicit JSON
   `FLOOD_LAB_ALLOWED_ORIGINS` list.
5. Independently grant the authenticated tenant/object ID access to an explicit
   run. Token roles, fictional personnel names and supplied run IDs alone are
   never authority.

Example command shape (substitute approved IDs and a future UTC expiry; grants
expire within 30 days):

```bash
python -m flood_lab.cli grant --run-id "$RUN_ID" \
  --tenant-id "$TENANT_ID" --object-id "$PARTICIPANT_OBJECT_ID" \
  --principal-kind user --role participant --expires-at "$UTC_EXPIRY"
# Review the preview, then repeat with --apply.
```

Use `--principal-kind service --role api` for request creation, and `--role
observer` for read-only access. `--revoke --apply` revokes a grant; authorization
is checked again even when reconciling an existing idempotency receipt.

Separately authorize a pre-provisioned SQL injector or observer:

```bash
python -m flood_lab.cli sql-grant --run-id "$RUN_ID" \
  --principal-name "$SQL_PRINCIPAL" --capability inject --expires-at "$UTC_EXPIRY"
# Review, then repeat with --apply. SQL role membership must already be provisioned.
```

Missing auth configuration returns `503 setup_required`; there is no development
sign-in bypass or environment flag that disables JWT checking.

## Run the independent API and UI

```bash
PYTHONPATH=src .venv/bin/uvicorn flood_lab.api:app \
  --host 127.0.0.1 --port 8088 --no-access-log
```

`/health` is liveness only and honestly reports `graph_sending=false`; it does not
claim authenticated database readiness. `/docs` and `openapi.json` describe
`flood-lab/v1`. All operational paths require Entra and run grants.

Set these **public**, build-time UI values:

```text
VITE_ENTRA_TENANT_ID=<approved tenant UUID>
VITE_ENTRA_CLIENT_ID=<approved SPA application UUID>
VITE_ENTRA_API_SCOPE=api://<approved API application UUID>/FloodLab.Access
VITE_API_BASE_URL=<approved HTTPS API origin or explicit loopback HTTP address>
```

Then `npm --prefix ui run dev`, or `npm --prefix ui run build` and serve
`ui/dist/` with your approved host. Missing/invalid settings show **Setup
required**; the build never substitutes a test identity. Test harness files under
`ui/test/` are not entries in the production build.

The UI lists only authorized runs/requests, permits participant acknowledgement
and allocation, preserves inputs after a conflict, and offers **Read latest
record**. Before sending a write it journals the exact key/precondition/inputs
in account-scoped session storage. An unknown result blocks new UI operations
until the original is reconciled. Keep that browser session; closing it can
remove its journal, so capture the key/correlation ID before closing if a result
is still unknown. Operators can inspect retained receipts/events.

No participant seed, reset, approval, injection or send controls exist.

### Independent images

From this directory only, on a supported Linux x64 host:

```bash
docker build -f Dockerfile -t flood-lab-api:local .
docker build -f Dockerfile.ui -t flood-lab-ui:local \
  --build-arg VITE_ENTRA_TENANT_ID --build-arg VITE_ENTRA_CLIENT_ID \
  --build-arg VITE_ENTRA_API_SCOPE --build-arg VITE_API_BASE_URL .
```

Supply API environment at runtime via your approved secret mechanism. The
separate UI image serves port 8080; set `FLOOD_LAB_BROWSER_API_ORIGIN` to the
**same exact approved API origin** for its CSP. Do not expose Vite's development
server in production. No image migrates, seeds, deploys or provisions anything on
startup, and Game Theory's image/build must not include this directory.

## Operational contract

The API exposes:

- `GET /v1/runs` with `limit <= 50`, bounded offset.
- `GET /v1/runs/{run_id}/shelters` (maximum 100 seeded shelters).
- `POST /v1/runs/{run_id}/requests` for explicitly granted service principals.
- `GET /v1/runs/{run_id}/requests` with `limit <= 100`, sequence cursor/status.
- `GET /v1/runs/{run_id}/requests/{request_id}`.
- `POST .../{request_id}/acknowledge` and `POST .../{request_id}/allocate`.
- `GET /v1/runs/{run_id}/events` with `limit <= 100`, sequence cursor and optional
  record filter. Continue `next_after` until null before claiming complete evidence.

Every REST write requires `Idempotency-Key` in a header, never JSON. Conditional
writes also require the strong quoted `If-Match` header. Response
`record_version` is the opaque value without quotes; the `ETag` response header
adds those quotes. The REST catalog's reserved `expected_version` parameter is
that unquoted value: a future adapter converts it to `If-Match`, not a body/query
field. The dispatcher generates the idempotency key from stable run/operation
identity; catalogs cannot choose it or arbitrary headers. The participant UI
generates and journals its own per-action key. No weak, wildcard, numeric or
multi-value ETags.
Inputs are bounded, quantities are strict integers, timestamps require explicit
UTC (`Z` or `+00:00`), and record/run relationships are checked in both service
logic and composite database foreign keys.

Idempotency identity is **authenticated actor + run + operation + key**, with
case-sensitive keys. The fingerprint covers the complete normalized payload,
record ID and precondition. Replay occurs **before** checking the current record
version and returns the original status/body/ETag/event/correlation even if the
record has since changed or recovery retired it. Different inputs with the same
key conflict. Revoked run access still prevents replay.

All sanctioned writers acquire the same transaction-owned, bounded SQL Server
run lock; database uniqueness is a second concurrency safeguard. Mutation,
receipt and event commit in one transaction. Never blindly retry with a new key:

- **succeeded**: committed target mutation with durable event and receipt.
- **rejected**: known authorization/input/precondition rejection; not success.
  Authorized in-run business rejections receive a retained receipt/event.
- **failed**: known pre-dispatch/setup/read failure; no success is claimed.
- **unknown**: a write result could not be confirmed, including connection/commit
  errors. Reconcile identical inputs/key; inspect durable evidence if still unknown.

The HTTP correlation header matches the original durable receipt on replay.
Errors are sanitized; validation values, bodies, tokens, URLs and SQL parameters
are not logged. SQLAlchemy hides parameters and Uvicorn access logs are disabled
in documented/container commands.

### Occupancy injection

Only the two fixed procedures are allowlisted:
`flood.ReadOccupancy` and `flood.UpdateOccupancy`. SQL inputs are parameterized;
the caller cannot select a query, SQL fragment or procedure name. Procedures use
the caller's actual SQL principal/run grant, not an actor supplied as input.

SQL operation descriptions are now **version 2**. Additive migration
`0002_occupancy_percentage` updates those procedures while retaining the original
migration, role grants, records, events and immutable receipts. Preview and apply
`migrate` before registering the new catalog as a new configuration revision.

`ReadOccupancy` returns **`occupancy_percent`**, a numeric value on a **0–100**
scale computed by SQL using floating-point division, not integer division or a
rounded display value. In the ordinary generic board condition, select that read
result, the **greater-than (`gt`)** operator, and numeric literal **85**. Exactly
85% does not trigger. No mathematical expression or flood-specific product
behavior is needed.

The read's `durable_event_id` and `committed_at` outputs are **optional**. An
out-of-band human change can leave no succeeded event matching the current
record version. The procedure then returns null evidence rather than generating
an ID or substituting the read time. `evidence_from_result` treats missing,
incomplete or invalid event evidence as absent; detection timing remains
**indeterminate**, even when the known occupancy percentage exceeds 85.

New occupancy mutations persist the computed percentage in their idempotency
receipt. Reconciliation reads that original snapshot, never the current shelter
value. Pre-upgrade immutable receipts may lack this added field, so the
mutation catalog marks it optional; such receipts are not rewritten or
backfilled. The read percentage is always present for an available valid shelter.

```bash
python -m flood_lab.cli read-occupancy --run-id "$RUN_ID" --shelter-id "$SHELTER_ID"
python -m flood_lab.cli inject-occupancy --run-id "$RUN_ID" --shelter-id "$SHELTER_ID" \
  --occupancy 108 --expected-version "$ETAG" --idempotency-key "$INJECT_KEY"
# Review, then repeat the exact inputs with --apply.
```

Set the observer and injector URLs separately. Occupancy writes require no
ambient transaction: the procedure owns the entire commit and returns a durable
receipt. API/CLI callers do not interpret SQL connectivity errors as success.

## Demonstration timing and evidence

`assets/profile.json` contains the approved **demonstration**, not live policy:

- Capacity trigger is **strictly greater than 85%**; exactly 85% does not trigger.
  Bind SQL read result `occupancy_percent > 85` directly through the generic UI.
- Detection is within **2 minutes** of the committed injected-occupancy event.
- Acknowledgement is within **10 minutes** of committed request `created_at`.
- Adequate allocation is the requested quantity within **20 minutes** of that
  same request creation. A user-entered availability time cannot backdate proof.
- Deadline equality is timely; missing/truncated/inconsistent evidence is
  **indeterminate**.
- At most **one initial plus one escalation** per request; no escalation loop.

Database `SYSUTCDATETIME()` values are assigned within the mutation transaction,
and are evidence only once the containing event commits. They are the durable
exercise timestamps, not a claim to expose SQL Server's physical transaction-log
commit instant. Stored precision is six fractional digits, matching Python's
lossless microsecond boundary comparisons. Uncommitted/scheduled/client timestamps
never start assessment clocks. No automatic detector or timeout sender is running:
a detection observation must come from separately authorized durable observation
evidence, otherwise its assessment is indeterminate. Test clocks/identities are
explicitly labeled and confined to tests.

## Safe recovery, not universal reset

```bash
python -m flood_lab.cli recover --run-id "$RUN_ID" --owner-operation "$SEED_OWNER_OPERATION"
# Inspect every eligible/conflicting record, then repeat with --apply.
```

Recovery is restricted to that run's owning database principal **and SID** and
its original seed manifest. A preview does not authorize a stale write. Apply
rechecks the original expected versions, record ownership and live dependencies
under locks, in allocation → request → shelter order. It also compares the seeded
field snapshot, preserving out-of-band human changes that forgot to bump a version.
It reports each missing,
changed or referenced record and completes eligible records only.

Recovery **soft-retires** unchanged seed-owned data. It deliberately preserves
participant changes, occupancy injections, service-created requests, other
operations' references, grants, receipts, events and recovery evidence. A run
with retained active records remains active; partial completion is explicit and
CLI exits 2. Repeating recovery is safe and records a new audit attempt. There
is no universal clear endpoint and no automatic evidence purge. **Email cannot
be rolled back.** A separate retention/ownership decision is needed for eventual
disposal.

## Generated files and Game Theory UI setup

```bash
PYTHONPATH=src .venv/bin/python -m flood_lab.assets
PYTHONPATH=src .venv/bin/python -m flood_lab.openapi
```

The code-built `assets/` contains fictional shelters/personnel, Markdown
narrative without tables/HTML/images, objectives/evidence criteria, inject CSV,
request samples, fixed notification text, profile, deterministic example seed
manifest, operator/UI guide and checksum manifest. `openapi.json` is generated
independently.

Import **one connector kind per connection**:
`operation-catalog-sql.json`, `operation-catalog-rest.json`, and optional
`operation-catalog-graph.json`. They conform to the external
`operation-catalog/v1` description contract. Graph template descriptions have no
runtime handler, sender, recipients, trusted link or provider result.

Follow `assets/operator-and-ui-setup.md`: create inventory/configuration,
register the separate catalogs, create a **blank** scenario, upload files,
author/import the narrative and objectives, publish, create a board, bind
operations, preview and obtain independent preparation review. **Execution stays
disabled.** A catalog cannot express arbitrary HTTP headers; future transport
support must set the fixed If-Match precondition from `expected_version`.
It must add ETag quotes, exclude that control value from JSON/query, and generate
`Idempotency-Key` independently. Remaining write fields are flat JSON and remaining
GET fields are query parameters. SQL procedure parameters remain separate: supply
their exact quoted version from `flood.ReadOccupancy`, not the REST token form.

For create-to-observe preparation, bind the later read's `request_id` to the
earlier create operation's declared output using the ordinary UI's typed
prior-result reference. Supply actual earlier board-step IDs, not invented
future target IDs. A reference names `source_step_id` and `field`; its source
must be guaranteed earlier, with an explicit dependency and a matching declared
type. Do not reference a mutually exclusive branch sibling. If a later REST
conditional operation needs `expected_version`, prefer the most recent
guaranteed earlier `resource-request.read` step's bare `record_version`. Preview
validates declarations only; it does not resolve live results, call the lab or
authorize execution.

## Verification and CI commands

After installing the lab's own locks, `bash scripts/verify.sh` runs the independent
non-service checks below and requires the foreign schema artifact. Add `--browser`
for the test-only browser matrix and `--sql` for the real database suite.
`--sql` fails immediately if the dedicated disposable target is not explicitly
configured; it cannot make a missing SQL service look like a passing CI gate.

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit
.venv/bin/ruff check src migrations tests
.venv/bin/ruff format --check src migrations tests
PYTHONPATH=src .venv/bin/python -m flood_lab.assets --check
PYTHONPATH=src .venv/bin/python -m flood_lab.openapi --check
npm --prefix ui run test
npm --prefix ui run build
npm --prefix ui run test:browser
```

The verification wrapper keeps browser files inside this lab. If it reports
Chromium missing, install that browser for **this** UI, then retry:

```bash
mkdir -p .local/build
export TMPDIR="$PWD/.local/build"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.local/browsers"
(cd ui && npx playwright install chromium)
bash scripts/verify.sh --browser
```

Browser tests reserve loopback port **4188**, separate from the main application's
browser harness. They use `ui/test/fixture.html` and explicitly labeled test-only identity/network
adapters, never a production auth bypass. They do not prove Entra or SQL readiness.

The foreign compatibility test reads the exported JSON Schema artifact at
`../../backend/contracts/operation-catalog-v1.schema.json`, never Game Theory
Python code. In an independent checkout set `FLOOD_LAB_CATALOG_SCHEMA` to a
separately supplied exported artifact. If absent, that gate is reported skipped,
not passed.

### Real SQL integration gate

Provision/select an **empty disposable** dedicated SQL Server database named
`flood_lab_test_<unique_suffix>`, distinct from both the exercise runtime DB and
the Game Theory application DB. Set:

```text
FLOOD_LAB_TEST_DATABASE_NAME=flood_lab_test_<unique_suffix>
FLOOD_LAB_TEST_DATABASE_URL=<explicit encrypted MSSQL URL for that database>
FLOOD_LAB_TEST_ALLOW_RESET=yes
```

Then run `PYTHONPATH=src .venv/bin/python -m pytest tests/sql`. The tests migrate
only that verified DB and use uniquely named TEST ONLY runs. They retain data
and evidence for review; the opt-in confirms the target is disposable, **not**
permission to reset any other database. No SQLite substitution or implicit host
discovery occurs. No configured URL means explicit skips, incomplete integration
evidence. CI must require a configured SQL target rather than accepting skips as
proof.

Coverage includes atomic rollback, duplicate/concurrent keys, stale versions,
post-commit lost-response replay, changed payloads, actor/run/record isolation,
grant revocation, least-privilege database roles, stored procedure idempotency,
exactly-85%/above-threshold percentages, missing-event indeterminate evidence,
recovery after human edits, other-operation references and repeatable cleanup.

### Standalone Linux CI recipe

The parent job must install ODBC Driver 18, start an approved Linux x64 SQL Server
service, and create a separate empty `flood_lab_test_ci` database. Supply
`FLOOD_LAB_TEST_DATABASE_URL` through that job's secret environment as an encrypted
`mssql+pyodbc` URL naming **that exact database**, never the Game Theory test DB.
The fixture needs DDL authority in this disposable database for migrations and
test-only role checks. Then:

```bash
cd exercises/flood-response
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
npm --prefix ui ci
mkdir -p .local/build
export TMPDIR="$PWD/.local/build"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.local/browsers"
export FLOOD_LAB_TEST_DATABASE_NAME=flood_lab_test_ci
export FLOOD_LAB_TEST_ALLOW_RESET=yes
(cd ui && npx playwright install --with-deps chromium)
bash scripts/verify.sh --sql --browser
```

The browser harness uses port 4188. The schema check reads only
`backend/contracts/operation-catalog-v1.schema.json`; the lab does not invoke
`gametheory preparation-schemas` or install/import Game Theory.

## Remaining authorized live gates

Select and authorize the dedicated host/database, network, distinct identities,
Entra registrations/consent, run grants, operational owner and retention policy.
Then run real SQL integration and an actual participant sign-in. Graph submission,
mailbox observation and Game Theory execution remain **unimplemented**, not merely
unconfigured. Choosing a real sender/recipient/link or approving preparation
cannot silently enable them. No cloud changes, deployment or email sending are
authorized by this package.

## Recorded bounded verification — 2026-09-22

On the implementation host (macOS arm64, Python 3.12.14, Node 24.1.0):

- `pytest -q tests/unit tests/sql`: **44 passed, 19 skipped**. Every SQL test
  explicitly reported the missing `FLOOD_LAB_TEST_DATABASE_URL`; no database was
  substituted or inferred. One upstream Starlette/AnyIO deprecation warning.
- `ruff check src migrations tests`: **All checks passed**.
- `ruff format --check src migrations tests`: **27 files already formatted**.
- Offline MSSQL checks compile every SQLAlchemy table/index and parameterized
  lock-hinted queries, and compare migration columns with the declared model.
- Occupancy follow-up: additive procedure migration and SQL operation version 2;
  direct `occupancy_percent > 85` binding, optional read event/time evidence,
  and missing-evidence indeterminate checks. Asset bundle version is 1.1.0.
  Real-SQL exactly-85%/above and missing-event cases remain explicitly skipped locally.
- Asset drift check: **16 independent lab assets checked**.
- OpenAPI drift check: **independent flood-lab/v1 OpenAPI current**.
- Foreign compatibility: all three separate catalogs validated against
  Game Theory's exported `operation-catalog-v1.schema.json`, with no runtime import.
- REST catalog fields were cross-checked against OpenAPI: path inputs are
  substituted, reserved unquoted versions map to required `If-Match`, generated
  idempotency keys use required `Idempotency-Key`, and only domain fields enter JSON.
- `pip check`: **No broken requirements found**. Both hash-locked requirement
  files omit private package-index metadata.
- UI Vitest: **14 tests passed**. TypeScript and Vite production build passed;
  JavaScript was 476.10 kB / 132.38 kB gzip.
- Playwright: **4 passed**, covering desktop/light and mobile/dark operations
  plus the normal setup-required entry point. Chromium was installed only after
  the explicit missing-browser failure, under the lab's ignored `.local/`.
- Production bundle checked for test identity/network fixture markers: **absent**.
- One mechanical design scan returned **no findings**. Desktop/mobile screenshots
  were directly reviewed; no nested agents were spawned. Test-only screenshots
  are under ignored `ui/test-results/`, not evidence of live participation.

No Docker daemon or approved disposable SQL host was available, so image builds,
migrations, stored procedures, real SQL concurrency/recovery and database roles
were **not executed here**. Real Entra sign-in/consent and target authorization
were not validated. Those remain live gates; fixture passes are not substitutes.
There were no root application changes, commits, deployment, cloud operations
or notification sends by this implementation.
