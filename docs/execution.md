# Environment policies and exercise execution

The execution path is opt-in. It does not change `plan_v1`, promote preparation
approval, provision targets, or give the planner external tools. The production
policy below supersedes the earlier flood pilot's blanket production exclusion
only for the separate executable contract. No actual production target is
activated by this code.

## Administrator Settings

Organization administrators use **Settings** in the application header:

- **Environments:** explicit classification, execution availability, independent
  approval requirement, and immutable policy history.
- **Run access:** explicit workspace operator/reviewer grants. Existing membership
  and preparation-review controls remain separate.
- **Runtime status:** configured capabilities, not a substitute for live readiness.

Production always requires independent execution approval. This is enforced by
the API and SQL constraints, not only a disabled checkbox. Once classified as
production, an environment cannot be downgraded; renaming or weaker connection
labels cannot bypass the rule. A target's configuration and operator binding must
agree with its environment classification.

Nonproduction defaults to no execution approval, but an administrator can require
it. A run involving multiple environments uses the strictest requirement.
Approval-exempt runs record the operator and current policy; they do not fabricate
an approval. An unresolved policy is shown as unresolved, never exempt.

Migration `0003` gives pre-existing environments an unknown, execution-disabled
policy; an administrator must explicitly classify them. New custom environments
also start unknown/disabled. Fresh bootstrap classifies the standard environment
labels explicitly, with all execution disabled. Classification or enabling a
policy never grants target access.

Policy changes append a revision under a resource `If-Match` precondition and
write audit in the same transaction. Collections do not invent collection ETags.
Policy changes invalidate a run's old authorization context. Tightening policy
blocks future effects; relaxing it does not silently restart a held run.

## Authority and immutable inputs

Execution uses `exercise-execution/v2`. The exported future-only v1 boundary and
preparation/catalog v1 contracts remain compatible; the old board `/execute`
endpoint remains disabled. Only the new run API dispatches work.

An explicit operator creates a run from a current saved preview. The immutable
effect manifest pins the scenario, assets, configurations, exact operation
versions, inputs, trigger/window, observation bounds, objective rules, and
recovery bindings. Later board/draft edits do not change an existing run.
SQL idempotency keys are explicitly dispatcher-owned in this contract. The
preparation editor shows SQL `idempotency_key` as supplied automatically and
offers to clear a typed value; the static preview lists it as unresolved, which is
expected. Run setup blocks creation while a typed value remains.

**Check before creating** (`POST .../boards/{bid}/runs/preflight`) evaluates the same
`RunCreate` body against the exact current preview, environment policies, operator
bindings, readiness receipts, and window. It returns every located setup issue and each
current blocker with a remedy, then persists nothing: no run, authorization, event,
audit record, or dispatch. It is advisory. Creation, authorization, start, and every
dispatch recheck everything again.

**Authorize under current policy** records a separate immutable authorization
context containing policy versions, resolved target identity/configuration
snapshots and externally verified readiness receipt IDs. The UI exposes those
records for review. No effect is dispatched by creating or authorizing a run.

Where required, an explicitly granted reviewer approves the exact run/context
with a bounded expiry covering the execution window. The reviewer cannot be its
operator, board creator, or preparation contributor. Administrator/editor/owner
roles do not imply either execution capability. Approval is revocable; removing
membership revokes the associated execution grants rather than reviving them
when membership is later re-added.

Start/resume, dispatch, and immediately pre-effect checks use current membership,
grant generation, policy, configuration withdrawal, target binding, readiness,
approval when required, window, and stop state. There is necessarily a boundary
after the final check at which a request is accepted by the provider: changing
policy or pressing stop cannot retroactively cancel that request.

## Operator target binding and readiness

Catalog registration is descriptive. Runtime targets are additionally allowlisted
by a read-only, operator-managed JSON file. API and executor deployments must
mount the same reviewed file at `GT_EXECUTION_BINDINGS_FILE`. It contains no
tokens, keys, passwords, or arbitrary script/SQL.

Generate the authoritative schemas:

```bash
gametheory execution-schemas
gametheory openapi
npm run contracts
```

`backend/contracts/execution-bindings-v1.schema.json` describes a list of bindings.
Each supplies the exact configuration ID/digest and environment ID, matching
resource/endpoint/database/identity reference, explicit user-assigned managed
identity client ID, REST token scope, bounded timeout/response size, and approved
operation digests. Operation digests hash canonical JSON of the **registered
operation including its materialized defaults**, not raw uploaded JSON text.
`safe_replay` is off unless an operator has verified the target's idempotency
contract; never enable it simply because retrying is convenient.

SQL calls use ODBC Driver 18, certificate validation, explicit managed identity,
parameterized procedure values, and no ambient transaction. This matches the
independent lab's transaction-owning procedures. The actual database name must
match, and the product application database cannot be an exercise target.
Procedure and parameter identifiers come only from the validated allowlist.

REST uses HTTPS, an operator-selected managed identity/token audience, bounded
relative paths and scalar bodies/query fields. Redirects and environment proxies
are disabled. `expected_version` is a bare token converted to quoted `If-Match`;
it is not sent in JSON. The executor generates stable `Idempotency-Key` values
from run/phase/step identity. Accepted-but-unconfirmed writes, including HTTP 202,
are unknown rather than successful. Writes must declare a durable event ID,
source commit timestamp, and outcome. The Graph service cannot be activated by
registering it as an ordinary REST target.

Identity permissions, network paths, server-side isolation, and target contents
must be independently verified by an authorized operator. Record an attestation
with:

```bash
gametheory execution-readiness --file /approved/path/readiness-receipt.json
```

This command **does not probe a target**. Its schema is
`execution-readiness-v1.schema.json`; it requires the exact binding digest,
checked/expiry times, a non-secret evidence reference, and coverage of
connectivity, identity, permissions, and isolation. It uses a separate SQL
operator principal. API/planning/executor SQL identities cannot insert readiness
records. Receipts must cover the complete execution window. Binding changes
invalidate old receipts; replacing a receipt does not silently change an already
pinned authorization context.

## Running and observing

Use **Exercise runs** on a preparation board. The guided forms read the pinned
preview, so operators choose steps, declared results, and recorded values from
lists instead of typing IDs or JSON:

1. Choose manual start or one-off scheduling at the pinned window's start.
2. **Watch for a condition:** pick a read step, a declared reading, a typed
   comparison, and explicit interval, give-up time, and check limits.
3. **How each goal is judged:** for each pinned scenario objective, optionally pick
   the evidence step/result and comparison, an optional time limit, the recorded
   start time, and whether the system's own timestamp or the observation time
   decides lateness. Unmeasured goals stay indeterminate.
4. **Undo plan:** keep manual accounting, or bind a registered write whose
   ownership and version checks come from the change's recorded results.
5. **Check before creating**, fix anything listed, then create the immutable run.
   Its **Launch checklist** shows the window, environments, approval requirement,
   target identities, readiness expiry against the window, attempt budget, undo
   plan, and every blocker with who can resolve it.
6. Authorize current policy, obtain a separate execution approval if required,
   then start/schedule.
7. Inspect step results, the evidence timeline, and objective findings. Pause or
   stop when needed; use explicit safe reconciliation for uncertain operations.

Run setup is kept while switching board tabs. **Settings file (advanced)** exports
and imports the same `RunCreate` bindings; imported values fill the forms and are
validated again. There is no second contract.

The initial runtime executes a bounded acyclic graph serially, with at most one
active run per board and at most 1,000 attempts across exercise and recovery.
Independent boards remain isolated. Conditions use typed results from guaranteed
successful predecessors, not arbitrary expressions. Fixed waits and scheduled
starts use durable timers; controls signal them without restarting the run.
Only reads can be polled, with explicit interval, timeout, and sample bounds.
Notification/MCP steps are rejected, not skipped as if they succeeded.

Observation binding shape in an exported settings file:

```json
{
  "step_id": "REPLACE_WITH_READ_STEP_UUID",
  "field": "quantity",
  "operator": "gte",
  "value": 10,
  "interval_seconds": 10,
  "timeout_seconds": 600,
  "max_samples": 60
}
```

Objective rules name an existing pinned objective, a step/result field, typed
comparison and literal or prior-result value. Optional `anchor_step_id`,
`anchor_field`, and `within_seconds` define an authoritative deadline.
`source_time_field` measures a declared source event; otherwise the assessment
uses the actual observation timestamp. Equality at the deadline is timely.
Point observations do not prove complete historical coverage: absent evidence,
missing clocks, and a late observation without an authoritative late source event
remain indeterminate. Unbound free-text objectives are not interpreted by a model.

### Authoritative milestone evidence

Timed objectives are only as strong as the clocks behind them: a completed record
seen later proves only that it was complete by that observation. A target that
can prove timing exposes bounded milestones as ordinary declared scalar results.
The independent lab's `resource-request.milestones@1` read is the reference
example. No manifest, observation, or objective-rule field changed for this, so
historic runs keep their digests.

- **Event identity.** A milestone is one durable target event, identified by the
  target's own event ID and committed timestamp, such as
  `acknowledgement_event_id` and `acknowledged_at`. Game Theory never invents an
  event ID or substitutes a read time for a missing one.
- **Deduplication.** Every successful sample of an observed read is stored as
  both `operation.succeeded` and `observation`, and repeated polls can see the
  same source event many times. Findings compare values, so repeated samples of
  one source event cannot change a finding. A target that aggregates must count
  each durable event ID once; the lab sums distinct allocation events and ignores
  receipt replays.
- **Observation time versus authoritative source time.** Observation time is
  when the executor recorded a sample. Authoritative source time is a declared
  datetime result set by the target's commit clock and named by
  `source_time_field`. A late observation of an on-time source event is met. A
  source time before the anchor or after its own observation is inconsistent and
  indeterminate.
- **Coverage.** Point observations do not prove what happened between samples.
  Without a source time, an observation after the deadline stays indeterminate.
  A target verdict can decide more only when the target proves coverage from its
  own complete history, as the lab does once its `as_of` is after the deadline
  and every allocation record pairs with a durable event.
- **Lateness.** A finding is unmet only from committed evidence: a satisfied
  predicate whose authoritative source time is after the deadline, or a target
  verdict of `false` compared with `eq true`. Absence, outages, and missing or
  null optional values stay indeterminate.
- **Clock consistency.** Anchor and source times must come from one target clock.
  The lab assigns every value from its database clock under its run lock, and its
  milestone read shares that lock before reading `as_of`. No later commit can
  then carry an earlier time, so decided verdicts do not change. A backwards step
  of the database clock is outside this guarantee; evidence later than `as_of` is
  reported as inconsistent (null).

Bind a target verdict directly, for example `acknowledged_on_time` `eq` `true`
with no anchor. Alternatively use the timestamp form: `acknowledged` `eq` `true`,
anchored on the read's `created_at` with `within_seconds`, and
`source_time_field` `acknowledged_at`. A paged event-history adapter for targets
that cannot compute bounded milestones remains future work (F4).

Persisted attempts distinguish succeeded, rejected, failed, and unknown. A lost
result after commit keeps the original identity, payload, and key; safe replay
recovers the target receipt rather than intentionally issuing another mutation.
There are no blanket durable retries around external writes. Safe reconciliations
are explicitly requested and bounded; unsafe writes remain for manual resolution.

## Stop and recovery

Pause blocks future dispatch but does not undo accepted effects. Stop is terminal:
when an accepted effect remains unknown the state is `stopped_incomplete`.
Another explicitly granted operator may stop or account for a run after its
original operator loses access; that does not grant permission to resume it.

Recovery is preview-first and separately authorized under the current policies,
within the manifest's time window. It processes confirmed writes in reverse
execution order. A recovery binding must reference a registered write and bind
both ownership and version preconditions from the original successful result.
The target must enforce those checks and preserve human changes.

Free-text recovery guidance is never executed. Unsupported recovery becomes
`manual_required`; an operator must use the external system's own approved tools
and record a non-secret evidence reference. Manual reports are labelled
`manually_accounted` / `recovered_with_manual_reports`, not automatically verified
success. Unknown stopped effects retain the same distinction. The independent
lab's operator CLI stays outside the product; its receipts and participant changes
are retained. Email cannot be undone.

## Deployment and compatibility

Apply application migrations through `0004` using the separate operator, then
review grants:

```bash
gametheory database-grants \
  --api-client-id API_CLIENT_UUID \
  --worker-client-id PLANNING_WORKER_CLIENT_UUID \
  --executor-client-id EXECUTOR_CLIENT_UUID
docker build --target executor -t gametheory-executor:REVIEWED_TAG .
```

The optional executor principal is distinct. The planning worker's grants are not
expanded to execution tables or external targets. Readiness insertion and DDL
remain operator-only. Grant helpers add table privileges, not broad roles, and
do not remove older unrelated grants; inspect effective permissions.

Configure `GT_EXECUTION_ENABLED=true`, a separate `GT_EXECUTION_TASKHUB`,
`GT_EXECUTION_BINDINGS_FILE`, and the existing tenant/SQL/Scheduler settings.
Run `gametheory-executor` separately from `gametheory-worker`. The API needs the
same public/non-secret execution configuration to evaluate readiness. Do not mount
target credentials or attach target identities to the API or planner. The existing
greenfield Bicep does not deploy this executor or activate exercise targets.
Provisioning, identity attachment, task-hub grants, networking, and rollout require
a separate concrete deployment approval.

The executor's `exercise_v1` orchestration has its own dispatch table, stable
instance IDs, leases, timers, and evidence. Keep its version pinned for active
histories, just as `plan_v1` remains pinned for planning. SQL is authoritative for
business state; a missing Scheduler history causes intervention, never an
automatic fresh run. No production HA/throughput or disaster-recovery claim is
made by a one-worker configuration.

## Validation boundaries

Unit/UI/browser tests cover policy, contracts, outcomes, predicates, controls,
conflict preservation, and both themes. They do not establish live identity.
The Linux `execution` CI job separately provisions disposable
`gametheory_test_execution` and `flood_lab_test_execution` databases, starts the
independent lab as a separate process with TLS, exercises actual SQL/REST effects
and post-commit reconciliation, and restarts the durable executor during a wait.
It also assesses timed objectives from the lab's milestone read with short TEST
ONLY windows (on-time, late, absent, and partial responses), a strict occupancy
threshold, and a lab outage that holds the run for intervention.
Its token exchange and readiness attestations are explicitly test fixtures.

No SQLite substitute, token bypass in product code, shared application/lab
database, or scenario installer is introduced. Live Entra/managed identity,
private networking, actual targets, deployment, and Graph sending remain
separate authorized acceptance gates.
