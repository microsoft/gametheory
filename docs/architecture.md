# Control-plane architecture

Game Theory is an organization's exercise **control plane**. Participants continue
working in their normal systems; there is no participant portal.

This milestone implements authoring and preparation, not exercise execution. A
published scenario revision is an immutable authoring snapshot, **not** permission
to run it. Connection records and configuration revisions describe inventory and
proposed integrations, not activated target access. Preparation approval is a
review of a pinned snapshot and is explicitly not authorization to execute it.

## Components and authority

| Component                                 | Responsibility                                                                                                  |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| React 18 / TypeScript / Vite / Tailwind   | Mineral/ocean document-first studio, shared-draft editing, review, accessibility                                |
| FastAPI / Pydantic                        | Authentication, workspace authorization, contracts, conditional writes                                          |
| Azure SQL                                 | Authoritative memberships, drafts, revisions, asset metadata, conversations, proposals, dispatch intents, audit |
| Private Blob Storage                      | Immutable uploaded asset versions                                                                               |
| Durable Task Scheduler                    | Orchestration history, activity delivery, retries and restart recovery                                          |
| Python Container Apps worker              | Dispatch reconciliation and Agent Framework planning activities                                                 |
| Operator-configured Foundry project/model | Model inference; no external mutation tools are exposed                                                         |

The separate packages under `exercises/` emulate external operational systems.
They are not imported or bundled by the product and do not share its application
database. Their domain-specific catalogs and scenario files reach Game Theory
only through ordinary user/admin UI configuration. There is no exercise installer
or built-in scenario catalog.

Web/API run together on App Service. The worker is a continuously running Container
App, not a minute-scheduled job. Its small SQL outbox reconciler closes the
application-database/scheduler dispatch gap; it is not a replacement workflow engine.
The initial template keeps one worker replica. Scaling requires measured SQL,
model, scheduler, and deployment limits rather than an unlimited-throughput claim.

## Authoring contracts

The versioned `ScenarioContent` object owns narrative document JSON, objectives,
flow nodes/edges, and exact asset-version references. React Flow edits this graph;
Mermaid source is generated from it. Mermaid is not a second executable language.
Tiptap embeds reference graph/asset IDs, not copies of their contents.

Draft updates and proposal application require one strong numeric `If-Match` ETag.
A stale request receives 409 and does not mutate the draft or consume a proposal.
Publication locks and validates the current revision, then stores its complete
snapshot. Referenced Blob versions never change. Comments refer to a saved draft
version. Draft versions and published revisions are intentionally distinct.

Shared editing uses optimistic concurrency, not CRDTs or real-time multi-user
merging. The UI preserves local input on conflict and asks before discarding it.
Unsaved text is not persisted across an explicitly confirmed browser reload.

Markdown import supports headings, paragraphs, lists, emphasis, code, blockquotes,
HTTPS/mailto links, and separators. It explicitly rejects raw HTML, tables, and
images. Import replaces narrative only, after confirmation. Markdown export uses
explanatory labels for embedded references. JSON export preserves the complete
scenario; Markdown is **not** a lossless scenario serialization.

Current request-size, asset-size, document-depth, and graph/object limits are
implementation safety budgets, not an assurance of validated large-scale capacity.
The first milestone does not implement a global system-count limit or an execution
scheduler for scenario graphs.

## Identity and access

The SPA uses Entra authorization-code flow with PKCE. The API verifies signature,
issuer, configured tenant, audience, expiry, not-before, and delegated API scope.
The scope is not a workspace grant. Membership is checked in SQL using tenant and
object ID, not email. No development authentication bypass exists.

An explicitly bootstrapped organization administrator can create workspaces and
environments, grant memberships, and create organization-level inventory. Workspace
owners manage members; editors author, upload, comment, publish, and request/review
proposals; viewers are read-only. Organization administrators retain owner access.
Guest access requires an existing authorized identity in this tenant; invitations
and temporary approval-only grants belong to a later milestone.

Inventory can be workspace-owned, organization-wide (including future workspaces),
or assigned to specified workspaces. No credentials are stored or exposed.
Every node's connection must be available to its workspace, and its environment
must match that connection. Unknown assets and cross-workspace references are rejected.

Preparation uses these same workspace boundaries, but approval is an additional,
explicit workspace capability. Administrator, owner, and editor roles do not
automatically confer it. Organization administrators manage configuration and
approver grants; a reviewer cannot approve a board they created or contributed to.
No role receives exercise execution rights in this milestone.

## Preparation contracts and boards

[Preparation contracts v1](preparation-contracts.md) defines the generic operation
catalog, immutable connection configurations, manifest boundary, and API surface.
An uploaded operation description is not executable code or an authorization
grant. SQL descriptions identify narrow stored procedures; REST descriptions
identify bounded relative operations; Graph descriptions identify fixed templates.
There are no arbitrary SQL/HTTP tools, remote schema references, or secret values.
Target metadata is not probed or resolved during registration or preview.

A board starts from one selected published scenario revision and pins its exact
scenario and asset inputs. Its proposed operations are configured explicitly,
separately from the authoring graph. Saving a board uses conditional writes;
preview freezes its saved version and referenced configurations into a
server-digested immutable manifest. Editing a scenario draft or uploading another
asset version does not change an existing board.

Static validation checks types, references, bounded flow and policy metadata.
Readiness findings distinguish missing inputs from live checks not performed.
Nonproduction labels do not prove authorization, isolation, connectivity, or
effective target permissions. Production/unclassified targets remain ineligible.
Before execution exists, the board reports disabled execution and no execution
evidence, not a simulated run or invented assessment.

Preparation approval pins the exact preview and digest, reviewer, explicit
expiry, and acknowledgement of unverified prerequisites. Current workspace
access, explicit approver grant, contributor history, configuration withdrawal,
and board version determine its current validity. Old snapshots and decisions
remain auditable; resolving missing information changes the manifest and requires
a new review.

Preparation and future execution manifests are separate contracts. No feature
switch promotes one into the other. Actual execution requires complete approved
targets, a fresh execution approval, dispatch-time authorization, and a separately
versioned durable orchestration/outbox. The existing `plan_v1` remains planning
only.

## Durable planning

1. SQL records the exact saved draft, prompt, requesting identity, and dispatch intent
   in one transaction. Only one active request per scenario is accepted.
2. Workers atomically claim dispatch intents with expiring, token-fenced leases.
   Stable scheduler instance IDs correlate to request IDs. Failed or uncertain
   dispatch is reconciled with bounded backoff and a visible queue error.
3. The versioned `plan_v1` orchestration invokes an activity. Model calls do not run
   in deterministic replay code. The activity rechecks membership and references
   before using context and before recording a result.
4. Agent Framework receives the saved plan and recent conversation context. It has
   no target-system tools. Output is validated as a complete scenario proposal;
   it cannot modify permission records or authorize actions.
   Uploaded-file bytes are not yet supplied to the model; the planner is explicitly
   instructed not to claim it has read assets or connected systems.
5. A conditional SQL transition publishes the proposal once. Applying it requires
   explicit editor review, current permission, and the unchanged base version.

An activity can repeat a model call if a process fails before its result is committed.
This can duplicate **model cost**, even though only one proposal is published.
Scheduler durability is neither external exactly-once execution nor automatic rollback.
Persisted orchestration names must not be renamed or incompatibly changed while
instances are active; introduce a new version and drain old workers when needed.

Model requests have a timeout and output-token budget; activity retries are bounded.
No model deployment is selected implicitly. When planning is disabled, the UI says
so and manual authoring remains available. Tests may replace model calls explicitly;
production never substitutes canned responses.

The pinned `agent-framework-foundry` package transitively includes the prerelease
`azure-ai-inference` package. This implementation uses its Foundry project chat
client, not the inference-provider integration or experimental durable-agent
hosting wrappers. Package-level stability is not a guarantee for every integration.
Live tenant/model compatibility remains a deployment gate.

## Assets, failures, and audit

An upload first commits a staged SQL record and audit intent. Blob upload is
immutable; only a successful, reauthorized finalization marks it usable. Failed or
uncertain uploads remain visible as staged. Ready versions are never overwritten.
Downloads verify recorded size and SHA-256 before serving bytes; storage changes
outside the application produce an explicit integrity error.
The operator cleanup command fences old staged versions before deleting their
application-owned Blob keys. It never touches ready/published artifacts.

Publication and draft changes write audit metadata in the same SQL transaction.
Audit stores IDs and operations, not snapshots or secrets. API request IDs,
planning request IDs, scheduler instance IDs, and worker log fields correlate work.
Curated application logs avoid provider response bodies; no sensitive prompt/result
tracing is enabled. Foundry registration, Agent 365, comprehensive monitoring,
retention policy, and production audit hardening remain later work.

## Cloud and deployment boundaries

Commercial Azure is the implemented runtime path. `AZURE_CLOUD` chooses a neutral
profile; `AZURE_CLOUD_PROFILE` can specify explicit authorities, audiences, and
capabilities. Government authentication authority is represented, but storage/model/
scheduler compatibility is not assumed. Planning is explicitly blocked outside
commercial Azure until a supported implementation is added.

Government/custom support may require a different execution host/backend, not
merely endpoint substitution. Domain and authoring logic are separate from SDK
orchestration code, but there is no speculative second runtime. Nothing silently
routes Government workloads to commercial services.

The local scheduler emulator holds backend state in memory. Worker restart tests
leave that emulator alive; they do not prove managed-scheduler disaster recovery.
The repository includes SQL/emulator integration tests and deployment templates,
but template compilation or fixture-based browser tests do not establish a live,
production-ready deployment.
