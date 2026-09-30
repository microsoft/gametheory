# Roadmap and current status

Status as of 2026-09-30, with `main` at `7bc9ba5`. Update this file in the pull
request that changes an item's status.

This roadmap sequences the work described in the
[flood-response pilot plan](flood-response-pilot-plan.md), the
[deployment gates](deployment.md), and the [execution contract](execution.md).
Those documents remain authoritative for behavior and approvals. Listing an item
here does not approve provisioning resources, granting permissions, contacting
targets, or sending email.

## Where we are

Code for pilot build steps 2 through 4 is complete and proven in CI with real SQL
Server, the Scheduler emulator, and the isolated HTTPS lab. Step 1's resource and
policy decisions remain open, and nothing has run against live targets yet. The
studio runs in a new commercial tenant with planning on; its live acceptance is
open (P1). Execution stays disabled until an executor deployment and approved
targets exist (P2, P3).

| Pilot build step ([§8](flood-response-pilot-plan.md#8-build-sequence-and-gates)) | Status                                                                                                                   |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| 1. Resolve scenario, resource, and policy decisions                              | Partial: scenario and demonstration profile selected; targets, identities, mailbox, recipients, and cost unapproved      |
| 2. Agree versioned operation and data contracts                                  | Done: `exercise-preparation/v1`, `exercise-execution/v2`, and the lab OpenAPI, including `resource-request.milestones@1` |
| 3. Boards, preview, approval, and an execution-disabled UI                       | Done (#9)                                                                                                                |
| 4. SQL injection and observation, API request, human response                    | Done in CI (#14, #15, #16); not yet run against approved live targets                                                    |
| 5. Narrowly scoped Graph sending                                                 | Not started; blocked on mailbox and recipient approval                                                                   |
| 6. Acceptance matrix, approved deployment, pilot enablement                      | Not started                                                                                                              |

## Item IDs

- **N**: a build increment in this repository (code, contracts, or templates).
- **A**: a live acceptance gate run against real Azure resources or targets.
- **F**: a future item that is not scheduled.

The original item list was not preserved. N2, N3, A11, and F4 keep the meaning
used in earlier pull requests and documents. IDs marked † were assigned when this
roadmap was rebuilt on 2026-09-30 and continue after the highest known number.
N1, A1–A10, and F1–F3 are not recorded; do not reuse them.

## Delivered

| Item | Pull request | Outcome                                                                                                               |
| ---- | ------------ | --------------------------------------------------------------------------------------------------------------------- |
| —    | #5           | Persisted scenario-authoring studio, Entra sign-in, durable AI planning proposals, commercial Azure templates, and CI |
| —    | #9           | Generic preparation boards, static previews, independent preparation approval, and the isolated flood-response lab    |
| —    | #14          | Administrator environment policies, run access grants, and the opt-in SQL/REST executor (`exercise-execution/v2`)     |
| N2   | #15          | Guided run setup, a non-mutating check before creating a run, and a launch checklist                                  |
| N3   | #16          | Authoritative acknowledgement and allocation milestones for timed objectives                                          |
| —    | #17          | Opt-in run-check assistant that suggests watches, goal rules, and undo bindings; live model validation is A11         |

## Phases

P2 and P3 can proceed alongside P1. P4 needs its own approvals. P5 depends on P1
through P4.

### P0: Repository health (now)

- Answer #4, the GitHub inside Microsoft migration notice, with `optin` or
  `optout`. Only a repository admin can respond. An unanswered repository is
  archived automatically, which would block pull requests and deployments.
- Resolve #1 (missing LICENSE) consistently with the #4 decision.
- Triage the open Dependabot updates: #6 (react-router) and #8 (vitest) for the
  studio, #18 (brace-expansion), #19 (@grpc/grpc-js), and #20 (moment) in the
  studio's root lockfile, and #11 (vitest), #12 (vite), and #13 (Playwright) for the
  lab UI.

### P1: Studio in the new commercial tenant (in progress)

The studio is deployed in a new commercial tenant in North Central US
(`rg-gametheory-ncus`); the previous tenant's West US 3 environment is still running.
Steps 1 through 4 are complete: planning is on, and the run-check assistant and
execution are off. The planning model is GPT-5.6-luna. GPT-6-luna returned HTTP 500
from the Foundry project Responses API that the planner calls; see the
[Foundry model notes](deployment.md#what-the-template-contains) before switching.
Follow [required operator inputs and gates](deployment.md#required-operator-inputs-and-gates)
and the [run-check assistant rollout](deployment.md#run-check-assistant-rollout):

1. Confirm regional capacity, then create the Entra registrations and the
   infrastructure with `deployApplications=false`.
2. Build images from `c87b9c4` or later and deploy them by digest.
3. Apply migrations through `0005`, run `gametheory database-grants`, and bootstrap
   the first administrator.
4. Start the applications with planning off. Enable planning, and optionally the
   run-check assistant (`enableRunAssistant=true`), only after the SQL, identity,
   network, and model checks pass. Execution stays off. If A11 shows truncated
   proposals, raise `plannerMaxOutputTokens`.

| Item | Live gate                                                                                                                                  | Status  |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------- |
| A11  | The deployed model produces a real planning proposal that is reviewed and applied, plus a run-check suggestion if the assistant is enabled | Partial |
| A12† | The studio [live acceptance](deployment.md#live-acceptance) checklist, including `backend/tests/test_live_api.py`                          | Partial |

A11: from inside the application network, the deployed model returned a schema-valid
planning proposal with a measurable objective in 5.1 seconds. A proposal that is
reviewed and applied in the studio is still required. The run-check assistant is off,
so no suggestion is needed yet.

A12: private DNS for Blob, Scheduler, and Foundry, a managed Scheduler activity, and a
private Blob round trip passed. Sign-in, authoring, SQL access by the runtime
identities, `test_live_api.py`, and the rest of the checklist are open.

Decision for the owner: whether and when to retire the previous tenant's West US 3
environment after A12 passes.

### P2: Deployable executor

`infra/main.bicep` includes the executor behind `deployExecutor` and
`enableExecution`, which default to off; see the
[opt-in executor rollout](deployment.md#opt-in-executor-rollout).

| Item | Scope                                                                                                                                                                                                                                                                                                                                                                                                        | Status                                 |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------- |
| N4†  | Add the executor to `infra/` behind flags that default off: its own managed identity, a separate execution task hub with a task-hub-scoped grant, the Container App, a read-only bindings-file mount from a private share, and matching non-secret API settings. Document `database-grants --executor-client-id`. `enableExecution` defaults to false; turning it on needs the separate deployment approval. | Template done; deployment not approved |

Deploying N4 requires a separate, concrete deployment approval. Target identities
are never attached to the API or the planning worker.

### P3: Flood lab in Azure (Track B, live)

Resolve the lab decisions in
[§7](flood-response-pilot-plan.md#7-resource-discovery-and-authorization-gates):
the target subscription and SQL server, a dedicated exercise database with its SKU
and cost ceiling, API and UI hosting and network paths, named operators,
participants, and approvers, retention, and cleanup ownership. Earlier Game Theory
deployment approval does not cover the lab.

| Item | Scope                                                                                                                                                                                                                 | Status                  |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| N5†  | Reproducible lab infrastructure: a dedicated exercise database, API and operational UI hosting, managed identities, narrow SQL permissions, private networking, health checks, logs, and deployment receipts          | Blocked on §7 decisions |
| A13† | Target readiness: a classified nonproduction environment, reviewed bindings, and [readiness receipts](execution.md#operator-target-binding-and-readiness) covering connectivity, identity, permissions, and isolation | Not started             |
| A14† | The first live nonproduction run without email: inject, observe, request, human response, milestone assessment, and recovery                                                                                          | Not started             |

A14 depends on P1, a deployed N4, N5, and A13.

### P4: Exercise email through Microsoft Graph (build step 5)

Approve the Microsoft 365 tenant, sender mailbox, recipient allowlist, and mailbox
access restrictions described in the
[Graph email boundary](flood-response-pilot-plan.md#5-graph-email-boundary).

| Item | Scope                                                                                                                                                                                                                                                           | Status                                    |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| N6†  | A narrowly scoped notification connector: a fixed template, visible exercise labeling, allowlisted recipients, a bounded budget with at most one escalation, a visibly disabled state, and reconciliation of unknown send outcomes instead of automatic resends | Blocked on mailbox and recipient approval |
| A15† | A live send to the approved mailbox, confirmed by a manual mailbox check                                                                                                                                                                                        | Not started                               |

### P5: Acceptance and pilot enablement (build step 6)

| Item | Scope                                                                                                                                                       | Status      |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| A16† | Run the [acceptance matrix](flood-response-pilot-plan.md#9-acceptance-matrix) against live nonproduction targets and record actual outcomes and limitations | Not started |

Then deploy the approved immutable artifacts, record the receipts, and let the
owner decide whether to enable the pilot.

## Later

Documented deferrals that are not scheduled. An item receives an ID when it moves
into a phase.

- F4: a paged event-history adapter for targets that cannot compute bounded
  milestones; see [authoritative milestone evidence](execution.md#authoritative-milestone-evidence).
- Government and custom-cloud deployment, and multi-cloud validation.
- MCP steps, which run creation currently rejects.
- High availability and scale beyond one worker, disaster recovery, backup and
  restore testing, and Scheduler history retention.
- Foundry agent registration and optional Agent 365 integration.
- Production exercises. Production approval is enforced, but no production target
  is authorized.
- Complex multi-agency branching and automatic mailbox reading.

## Not planned

These are deliberate boundaries, not backlog:

- A participant portal inside Game Theory; participants use their own systems.
- Built-in scenario templates, starter kits, or scenario installers.
- Arbitrary SQL, HTTP, or MCP execution, or a broad connector marketplace.
- Claims that irreversible actions, such as sent email, can be rolled back.
