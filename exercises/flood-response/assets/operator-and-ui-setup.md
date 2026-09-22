# UI-only preparation and independent lab operation

EXERCISE ONLY. This package never installs, seeds or calls Game Theory.

## Prepare the independent lab

Follow the package README to provision an explicitly selected dedicated local
SQL Server database and separate database identities. No production or cloud
resource changes are part of this package. Preview migrations and seed, then
apply explicitly. Record the returned run ID, seed owner operation and manifest
checksum. Repeating seed never overwrites existing data.

Configure the independent Entra API and SPA registrations under separately
approved identity setup. The API validates tenant, issuer, audience, signature,
expiry and delegated scope or application role. The operator separately grants
expiring actor/run permissions. A participant can acknowledge and allocate;
only an API service can create requests; observers only read. SQL injectors and
observers receive explicit database-principal/run grants.

## Configure Game Theory through the normal UI

1. Create separate SQL, REST and optional Graph inventory connections in the
   intended workspace. Use nonproduction classification, not production.
2. Configure concrete approved target metadata and identity references only
   when known. Unresolved values remain empty and explicitly unverified.
3. Register operation-catalog-sql.json on the SQL connection and
   operation-catalog-rest.json on the REST connection. Do not combine kinds.
   The SQL read/update descriptions are version 2; apply the lab's additive
   0002_occupancy_percentage migration first. Register a new configuration
   revision rather than overwriting an already pinned preparation snapshot.
4. Optionally register operation-catalog-graph.json as a description-only
   preparation artifact. Sending is absent; do not claim a successful send.
   Pin the chosen immutable initial or escalation template for each separate
   notification configuration. Leave sender, recipients and link unresolved
   until explicitly selected and approved.
5. Create a blank scenario. Upload the generated brief, objective criteria,
   inject schedule, fictional data, request samples, profile and templates.
   Import situation-brief.md with the normal Markdown importer.
6. Author objectives and a bounded flow: observe occupancy, create request,
   wait for acknowledgement or timeout, describe at most one escalation,
   then assess adequate allocation. Participants act in the independent lab UI.
7. Publish the intended scenario revision. Create a board from that publication,
   not from the mutable draft.
8. Explicitly bind proposed steps to registered operation versions. Supply
   existing run and shelter IDs from the independent operator output. IDs are
   correlation, not grants.
   For the capacity condition, bind the SQL read's occupancy_percent number,
   choose greater-than (gt), and enter the literal 85. Do not enter a formula
   or compare to 0.85: the returned scale is 0 to 100.
   For create-to-observe flow, bind the later request read's request_id to the
   earlier create operation's declared request_id output, rather than guessing
   a future ID. The typed prior-result reference has source_step_id (the actual
   earlier board-step ID) and field set to request_id. Add an explicit dependency
   so the source operation is guaranteed to precede the consumer.
   Where a later REST conditional operation needs expected_version, select the
   most recent guaranteed earlier resource-request.read step's record_version.
   That output is already an unquoted opaque value; do not interpolate strings
   or copy a stale version.
   A mutually exclusive branch sibling is not a valid source. Participants
   still acknowledge and allocate only in the independent operational UI.
   Preview checks the reference declaration; it neither resolves live values nor
   calls an operation, and a reference is not execution evidence or authority.
9. Enter the finite time window, demonstration thresholds, notification budget
   and scoped recovery policy. Pin exact asset versions and checksums.
10. Preview and inspect effects, proposed recipients, target identities, missing
    prerequisites and recovery conflicts. No preview contacts target systems.
11. Request review by a separately granted approver who did not contribute to
    preparation. Approval is preparation-only; execution remains disabled.

## REST transport caveat

Catalog fields are flat bindable scalar inputs and outputs, not an executor.
List endpoints remain in OpenAPI, not in the restricted catalog. On conditional
REST writes the declared expected_version is a reserved opaque string without
ETag quotes. A future approved adapter adds quotes and sends only the strong
If-Match header; expected_version is never sent in JSON or query parameters.
The dispatcher generates Idempotency-Key from stable run/operation identity for
writes. It is not a catalog-selected input. The independent UI generates its
own stable per-action key and retains it through reconciliation. Remaining
write parameters are flat JSON; remaining GET parameters are query fields.
Catalogs cannot select arbitrary headers. Registering a catalog does not create
a transport, provide a token or confer participant authority. SQL procedure
parameters are separate: pass the exact quoted version returned by ReadOccupancy.

## Observe and recover

Use durable committed database timestamps; a planned inject is not evidence.
ReadOccupancy does not invent event IDs or times. Its durable_event_id and
committed_at can be missing when no succeeded event matches the current record
version. Missing evidence makes timing indeterminate even when the percentage
exceeds 85. Do not substitute the read time, a generated UUID or a default date.
Read complete bounded event pages and retain event IDs. Unknown write outcomes
must be reconciled with exactly the same actor, run, operation, key and inputs.
Do not mint a new key to guess whether a mutation succeeded.

Preview recovery for the seed owner operation and explicit run. Apply rechecks
ownership, original versions and live references under a run transaction lock.
It retires allocations before requests before shelters. Changed records and
records referenced by other operations remain, with per-record conflicts.
Recovery soft-retires only unchanged seed-owned data. Participant and later
service operations, events, receipts and recovery evidence are retained.
Partial completion is expected after human activity. Use a new run key for a
new exercise, never force-clear the database or infer that email was undone.

## Remaining live gates

The dedicated SQL host, networking, identities, permissions, operational owner,
retention policy, trusted link, sender and recipients require explicit approval.
Real Entra sign-in, target authorization and real SQL concurrency are separate
validation gates. Graph submission and mailbox observation are absent and would
require a later implementation and authorization. No fixture proves readiness.
