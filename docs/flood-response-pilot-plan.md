# Flood response pilot: product and exercise-environment handoff

Status: **Preparation and independent-lab development selected; not deployment
or execution authorization.**

The user proposed two coordinated tracks: extend Game Theory, and establish a
controlled Azure environment containing databases, APIs, and Microsoft Graph
email integration to exercise the complete workflow. Reuse suitable existing
infrastructure rather than provisioning every service from scratch.

The user selected shelter capacity and resource escalation, with code-built
external systems and supporting files in `exercises/flood-response/`. Target
resources, identities, mailboxes, recipients, costs, and execution permissions
remain unapproved. Do not interpret this document, earlier Game Theory deployment
approval, or a published scenario as approval to modify those targets.

The first increment is preparation-only: generic connection configuration,
pinned boards, static previews, and explicit independent approver grants with
no self-approval. Preparation sign-off does not authorize execution, and a fresh
execution approval will be required later. The external exercise has its own
SQL/API/operational UI; it is not a Game Theory participant portal.

Game Theory must not contain flood-specific product logic, built-in scenario
templates, a starter kit, or a scenario installer. The user configures
connections, registers external operation descriptions, uploads files, and
authors the scenario and plan through the normal UI.
[Generic preparation contracts](preparation-contracts.md) define that boundary.
The end-to-end execution workflow below remains a later, separately gated goal.

## 1. Product intent and existing foundation

Game Theory is the exercise control plane. Scenario owners author and approve
plans there; participants respond through their normal systems. There is no
participant workflow to build inside Game Theory.

The current application provides:

- React 18, TypeScript, Vite, Tailwind, and the custom glass design system.
- Python/FastAPI, Entra authentication, workspace authorization, and Azure SQL.
- Scenario documents, objectives, flow diagrams, immutable asset versions,
  published revisions, comments, and explicit AI proposal review/application.
- Durable Task Scheduler and Container Apps workers for planning activities.
- Connection inventory kinds `sql`, `rest`, `graph`, and `mcp`, but no activated
  target-system connectors or exercise execution.

The commercial deployment currently uses West US 3. This is context, not a
requirement to move existing target servers or permission to reuse them.
Read [architecture.md](architecture.md), [development.md](development.md), and
[deployment.md](deployment.md) before changing application behavior.

Do not rebuild the authoring foundation. Existing scenario diagrams are not
executable contracts, and uploaded file contents are not currently provided to
the planner. Preserve those distinctions until explicitly implemented.

## 2. First end-to-end exercise

Use fictional shelters, occupancy figures, resource requests, and personnel.

| Stage   | Action or observation                                                     | Expected evidence                                                         |
| ------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Prepare | Seed a run-specific shelter dataset and validate approved targets         | Seed version, resource identities, connectivity and permission results    |
| Inject  | Set one exercise shelter's occupancy above its configured threshold       | Authorized operation ID, before/after values, target record version       |
| Observe | Read the shelter and evaluate the capacity condition                      | Source record version, observation time, evaluated threshold              |
| Request | Create a resource request through the exercise API                        | Stable request ID correlated to the run and operation                     |
| Notify  | Send an exercise-labelled email to approved test recipients               | Dispatch attempt and provider acceptance or explicit uncertain outcome    |
| Respond | A person acknowledges and allocates resources in the operational test app | Authenticated actor, acknowledgement/allocation times and record versions |
| Assess  | Observe the response through the API and evaluate objective criteria      | Evidence-linked met, unmet, or indeterminate results                      |
| Recover | Clean up eligible exercise data or flag conflicts for review              | Per-record recovery result and unresolved items                           |

The selected editable demonstration profile uses:

- Identify an occupancy breach above 85% within two minutes of the committed
  injection event.
- Receive an acknowledgement within ten minutes of request creation.
- Record the requested resource quantities within twenty minutes of request
  creation.

These are synthetic example values, not approved live policy. The notification
budget is one initial notification and at most one escalation; actual sending
remains disabled until separately authorized.

Define each measurement's clock and evidence source before implementation.
Observation delays and missing evidence must not be interpreted as proof of
participant failure. Use shorter configurable windows for automated tests.

The first branch should be simple: acknowledgement before the deadline versus
no acknowledgement. An escalation branch may send one separately approved
notification; it must not create an unbounded notification or retry loop.

## 3. Two implementation tracks

### Track A: Game Theory

Deliver a narrow but complete run workflow:

1. Create a game board from a published scenario revision.
2. Bind supported steps to approved connections and operation contracts.
3. Preview the target records, intended effects, recipients, limits, and recovery
   behavior; validate prerequisites without making external changes.
4. Approve the exact execution manifest for a bounded execution window.
5. Execute through a versioned durable orchestration and typed connector activities.
6. Display observed state, evidence, failures, and uncertain outcomes on the board.
7. Assess objectives and perform explicit, conflict-safe recovery.

The immutable execution manifest should pin at least:

- Scenario revision and exact asset versions.
- Typed operation versions, validated parameters, conditions, and bounded waits.
- Connection configuration version, concrete target identity, and environment.
- Notification template version, sender, and explicit recipient set.
- Execution limits, permitted time window, and recovery strategy.

Approval binds to the manifest digest. Changes require a new preview and approval.
Approval does not replace permission checks at dispatch and before external
effects. Revocation or a tightened environment policy must block subsequent work.
Current editor rights must not automatically become execution or approval rights.
Define those rights explicitly; keep separation-of-duties configurable only after
its policy is agreed, not as an implicit bypass.

Keep the model in proposal generation. It must not issue arbitrary SQL, construct
unrestricted HTTP calls, select unapproved recipients, or authorize execution.
Validate execution semantics separately from the authoring graph. For this pilot,
support a bounded acyclic flow; reject unsupported cycles and step types.

Use a capability gate that remains off until the target configuration and live
acceptance gates pass. Production-labelled connections remain ineligible.

### Track B: controlled exercise environment

Prefer one dedicated exercise database on a suitable existing SQL server, a
small Python API, and a separate operational test interface. Reuse approved
hosting/networking where appropriate.

Do not use Game Theory's application database as the exercise target. Do not add
tables to an existing business database by default. If no approved server has
suitable isolation, networking, and capacity, present that finding before
provisioning a replacement.

Provide:

- Repeatable migrations and seed data with a versioned manifest.
- Run-scoped synthetic shelters, resource requests, allocations, and event records.
- An authenticated API with OpenAPI documentation and bounded operations.
- A minimal operational test screen for acknowledgement and allocation, hosted
  separately from Game Theory. Existing suitable test tooling may replace it.
- Target-side idempotency, concurrency checks, and timestamps.
- Narrow database permissions for the injector, API, and observation paths.
- Seed/reset tooling restricted to owned exercise records, with preview and
  explicit application. No generic database-clearing endpoint.
- Reproducible Azure configuration, health checks, logs, and deployment receipts.

The lab emulates an organization's operational systems; its UI is not a new
Game Theory participant portal. Keep flood-specific fixtures and terminology
out of the generic Game Theory execution engine.

## 4. Agree shared contracts before parallel implementation

The following names illustrate the intended contract; they are not existing
endpoints or a frozen API specification.

| Surface         | Proposed minimal operations                                                   |
| --------------- | ----------------------------------------------------------------------------- |
| SQL connector   | Read shelter state; conditionally set occupancy for an owned exercise shelter |
| Exercise API    | Create/get a resource request; acknowledge; allocate; list relevant events    |
| Graph connector | Send a fixed exercise notification using approved sender and recipients       |
| Recovery        | Conditionally remove or restore eligible run-owned state and report conflicts |

Contract requirements:

- Use stable run, scenario-revision, operation, and target-record identifiers.
- Scope idempotency keys to the authenticated caller, run, and operation. Repeating
  the same request returns the prior result; a different payload using that key
  is rejected.
- Commit mutation and idempotency evidence atomically where the target supports it.
  Do not depend on the Game Theory database alone to prevent duplicate effects.
- Require expected record versions for updates and recovery. A human change must
  not be overwritten merely because the record belongs to the exercise.
- Enforce authorization server-side. A caller-supplied run ID or exercise label is
  correlation, not permission to access a record.
- Separate orchestrator permissions from human acknowledgement/allocation rights.
  Participants must not gain seed, reset, approval, or unrestricted mutation rights.
- Keep timestamps in UTC and return durable event identifiers.
- Define result states including succeeded, rejected, failed, and unknown; do not
  disguise provider failures as empty data or successful no-ops.
- Validate configured hosts and resource identifiers. Do not follow arbitrary
  target URLs or send credentials to redirects selected by scenario content.
- Expose sanitized operational errors while retaining correlation for diagnosis.
  Do not log tokens, credentials, full prompts, or uncontrolled message bodies.

Use allowlisted parameterized statements or narrow stored procedures for the
SQL pilot. Avoid arbitrary SQL text fields and generic unrestricted REST tools.
The worker's access to Game Theory storage must not imply access to every target.

## 5. Graph email boundary

Discover the existing Graph connection and its effective permissions before
deciding whether it is suitable for reuse. An inventory entry does not prove a
working connection, and an existing broad identity is not automatically safe.

Require:

- An explicitly approved Microsoft 365 tenant and designated sender mailbox.
- A deliberately small recipient allowlist enforced outside model-generated data.
- No arbitrary To, CC, BCC, sender, attachment, or forwarding inputs in this pilot.
- A visible subject prefix such as `[EXERCISE ONLY][Flood Pilot][Run ID]` and an
  equally clear body disclaimer. Do not impersonate a real emergency alert.
- A fixed notification template with validated synthetic fields and a trusted
  operational-app link.
- Verified mailbox access restrictions and least privilege, accounting for
  pre-existing grants. Confirm the current supported Microsoft configuration
  before applying it; do not grant tenant-wide access merely to make a test pass.
- A bounded notification budget, including any escalation.

The first version observes acknowledgement through the exercise API; reading
mail is not required. Provider acceptance of a send request is not proof of
delivery or that a person opened the message.

Graph sending must not be treated as automatically exactly-once. If a timeout
or process failure leaves a send outcome unknown, surface that uncertainty and
require reconciliation or an authorized decision rather than blindly resending.
Email cannot be cleaned up by rolling back the scenario database. Acceptance
includes a manual check of the approved recipient mailbox.

## 6. Durability, stop behavior, and recovery

Introduce a new versioned exercise orchestration; do not change persisted
`plan_v1` histories into execution histories. Keep network/model/database work
inside activities rather than deterministic orchestration code.

Use the existing transactional outbox pattern for run dispatch, with stable
instance IDs and reconciliation. Durable activity delivery does not guarantee
exactly-once external effects.

Define board states and operation states separately. The board must distinguish
normal waiting, intervention required, completion, and incomplete recovery.

Pause or stop blocks future dispatch at safe boundaries. It cannot promise to
cancel an already accepted provider request. Record in-flight actions and
reconcile their results before declaring a run stopped or recovered.

Recovery must:

- Use the pinned operation's recovery policy, ownership markers, and record version.
- Preserve human changes and records referenced by other operations.
- Handle dependent records in an explicit order.
- Report partial completion and conflicts, with an auditable manual resolution path.
- Preserve assessment/audit evidence according to the agreed retention policy.
- Never claim email has been undone.

## 7. Resource discovery and authorization gates

Before cloud mutations, inventory the explicitly selected Azure subscription and
candidate resource groups read-only. Record candidate server types, region,
network paths, identity support, capacity, and operational ownership.

Present a concrete resource proposal for approval covering:

| Decision                                                            | Current status                                                                |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Shelter-capacity pilot versus another flood workflow                | Shelter-capacity scenario selected                                            |
| Target subscription and existing SQL server                         | Not selected                                                                  |
| New exercise database, schema, SKU, and cost ceiling                | Not approved                                                                  |
| API/UI hosting and network access for participants and workers      | Not selected                                                                  |
| Exercise operators, participants, approvers, and execution policy   | Named identities unselected; explicit preparation approvers, no self-approval |
| Graph tenant, identity, sender mailbox, and recipients              | Not approved                                                                  |
| Thresholds, observation windows, notification budget, and retention | Demonstration profile selected; live policy and retention unapproved          |
| Deployment ownership and cleanup responsibilities                   | To assign                                                                     |

Earlier approval to deploy the Game Theory studio does not authorize creating
lab resources, adding new permissions, or sending email. Do not silently alter
firewalls, make private services public, change an existing server's configuration,
or relocate data to solve a connectivity issue.

Prefer managed identities. If a required integration cannot use them, propose
the credential-reference and secret-store approach separately. Do not place
credentials or access tokens in this handoff, source code, or deployment receipts.

## 8. Build sequence and gates

1. Review the working scenario and resolve the resource/policy decisions above.
2. Agree the versioned operation and OpenAPI/data contracts, including errors,
   idempotency, concurrency, and recovery. Record the agreed version for both tracks.
3. Build game-board persistence, preview, approval, and an execution-disabled UI
   while the lab implements the corresponding contracts and fixtures.
4. Integrate SQL injection and observation, then API request creation and human
   response. Use local tests and explicitly approved nonproduction targets.
5. Add narrowly scoped Graph sending only after mailbox and recipient approval.
   A disabled notification connector must be visibly disabled, not a fake success.
6. Run the acceptance matrix, deploy approved immutable artifacts, and document
   actual outcomes and limitations before enabling the pilot.

Track A owns product/domain behavior and connector adapters. Track B owns target
API/data behavior, test fixtures, infrastructure, and the operational test UI.
Neither track should independently change shared contracts. Keep migrations for
Game Theory and the exercise database separate.

## 9. Acceptance matrix

| Case                                              | Required outcome                                                                                                   |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Full flood-response loop                          | Real target mutation, condition observation, API request, approved email, human response, assessment, and recovery |
| Draft edited after board creation                 | The existing board continues to reference its pinned inputs                                                        |
| Target/recipient/operation changed after approval | Prior approval cannot authorize the changed manifest                                                               |
| Unauthorized user, target, record, or recipient   | Explicit denial before an external effect                                                                          |
| Production connection selected                    | Pilot validation rejects it                                                                                        |
| Duplicate delivery or crash after target commit   | SQL/API effects reconcile without duplicate mutation                                                               |
| Same idempotency key, different payload           | Explicit conflict; prior effect remains unchanged                                                                  |
| Worker restart while waiting                      | Run resumes its durable wait without restarting the exercise                                                       |
| Unknown email send outcome                        | No automatic duplicate send; visible intervention/reconciliation state                                             |
| Target outage, throttling, or expired access      | Bounded retries where safe, visible failure/uncertainty, no success-shaped fallback                                |
| Approval expiry, revoked access, or stop          | Future actions blocked; in-flight effects accounted for                                                            |
| No acknowledgement before deadline                | Only the approved bounded escalation branch executes                                                               |
| Human edits before recovery                       | Conflict is reported; newer human work is preserved                                                                |
| Missing observation evidence                      | Assessment is indeterminate rather than a fabricated success or failure                                            |
| Cleanup rerun and concurrent runs                 | Safe repeatability; other runs and unrelated records remain untouched                                              |
| Wrong identity or cross-workspace access          | No access to another workspace's boards, connections, or evidence                                                  |

Use real SQL Server/Azure SQL for SQL semantics, not SQLite as a substitute.
Label mocks, seeded responses, and accelerated clocks explicitly. A simulated
Graph send does not pass the live email acceptance gate. A successful provider
send response alone does not prove mailbox delivery.

## 10. Handoff instructions for new conversations

Start each conversation with this document and access to the current Game Theory
source. Prefer isolated branches/worktrees and a shared agreed contract version.
Do not assume an older default branch contains the current unmerged rebuild.
Establish the exact source baseline before implementation.

### Game Theory conversation

> Implement Track A of the flood-response pilot against the agreed contract.
> Reuse the current authoring, identity, persistence, and durable-planning
> foundations. Start with game boards, pinned manifests, preview/approval, and
> execution contracts. Keep execution disabled until authorized target setup and
> acceptance gates pass. Do not provision lab resources or send email based on
> this draft. Identify unresolved decisions before implementing dependent behavior.

### Exercise-environment conversation

> Implement Track B of the flood-response pilot against the agreed contract.
> Propose reuse of approved existing Azure infrastructure with a dedicated
> exercise database and synthetic data. Build the API, operational test interface,
> fixtures, idempotency, and safe recovery support. Keep cloud mutations and Graph
> permission changes/email behind explicit resource and recipient approval.
> Do not alter Game Theory product code or existing business datasets.

Each track should return its source baseline, contract version, changed files,
verification results, deployment/resource receipts where authorized, known
limitations, and remaining integration steps. Never include secrets.

Out of scope for this pilot: Production exercises, arbitrary SQL/HTTP/MCP
execution, a broad connector marketplace, automatic mailbox reading, irreversible
action rollback claims, complex multi-agency branching, large-scale guarantees,
Government deployment, and a general participant portal.
