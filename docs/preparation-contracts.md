# Preparation contracts v1

Game Theory prepares exercises; it does not execute them in this milestone.
Preparation approval is explicitly **not authorization to execute**. The product
does not contain an exercise catalog, installer, or domain-specific connectors.
Users register operation descriptions and author scenarios through the UI.

## Operation catalog: `operation-catalog/v1`

This is a restricted description format, not OpenAPI execution, JSON Schema
execution, SQL text, or an HTTP scripting language. External systems publish a
catalog alongside their own OpenAPI contract. Importing it does not contact them.
Unknown fields, duplicate keys/versions, malformed definitions, and unsupported
types are rejected.

The exported schemas are in `backend/contracts/`. Regenerate them with
`gametheory preparation-schemas` after changing the backend models. External
systems can validate against `operation-catalog-v1.schema.json` without importing
Game Theory code. The API additionally enforces semantic and authorization
checks that JSON Schema alone cannot establish.
`gametheory operation-catalog-schema` also exports the same catalog schema to
`backend/operation-catalog.schema.json` for consumers needing a standalone file.

```json
{
  "schema_version": "operation-catalog/v1",
  "name": "Example record service",
  "operations": [
    {
      "key": "record.read",
      "version": "1",
      "label": "Read an owned record",
      "effect": "read",
      "invocation": {
        "kind": "rest",
        "method": "GET",
        "path": "/runs/{run_id}/records/{record_id}"
      },
      "parameters": [
        { "name": "run_id", "type": "uuid", "required": true },
        { "name": "record_id", "type": "uuid", "required": true }
      ],
      "results": [
        { "name": "record_version", "type": "string", "required": true },
        { "name": "quantity", "type": "integer", "required": true }
      ],
      "recovery": "Read-only; no target mutation."
    }
  ]
}
```

Each operation has `key`, `version`, `label`, `effect`, `invocation`, `parameters`,
`results`, and `recovery`. Keys and versions are bounded identifiers, not URLs.
Effects are `read`, `write`, or `notify`.

Parameter/result fields have a unique `name`, a `type` (`string`, `integer`,
`number`, `boolean`, `uuid`, or `datetime`), and `required` (default true).
Optional constraints are `minimum`, `maximum`, `max_length`, and `choices`
(string choices). Values must match their types without coercing strings to
numbers/booleans. Numeric values must be finite. A `datetime` includes a timezone.
Parameter and result objects are flat; there are no remote references, arbitrary
expressions, headers, URLs, secrets, or executable schema hooks.

Invocation is one of:

- `{"kind":"sql","procedure":"schema.procedure"}`: a two-part allowlisted
  procedure identifier. No SQL text or arbitrary query operation exists.
- `{"kind":"rest","method":"GET|POST|PUT|PATCH|DELETE","path":"/relative/path"}`:
  a bounded root-relative path, with optional `{parameter_name}` segments.
  No scheme, authority, query string, fragment, redirects, traversal, or encoded
  path components. Placeholders must name declared parameters. A future adapter
  will substitute encoded scalar values, use remaining GET fields as query
  parameters and remaining write fields as a JSON object, and refuse redirects.
  Two control fields are reserved rather than sent in the body/query:
  `expected_version`, when declared, supplies the strong `If-Match` header;
  the future dispatcher generates `Idempotency-Key` from stable run/operation
  identity for writes. Catalogs cannot select other headers. Declared
  `expected_version` is a bounded string containing the opaque version value,
  without quotes; the adapter adds the ETag quotes.
- `{"kind":"graph","template_key":"fixed-template"}`: a fixed notification
  template, with sender, recipients and the immutable template asset supplied
  separately in administrator-controlled configuration. Parameters cannot
  supply sender, recipients, To/CC/BCC, attachments, or arbitrary message bodies.

All operations in one connection configuration must match the inventory
connection kind. MCP inventory remains unsupported for preparation bindings.
Catalog registration and nonproduction labeling do not grant target access.

## Connection configuration: `connection-configuration/v1`

An immutable configuration content object contains:

```json
{
  "schema_version": "connection-configuration/v1",
  "classification": "unknown",
  "resource_id": "",
  "endpoint": "",
  "database": "",
  "identity_ref": "",
  "catalog": { "schema_version": "operation-catalog/v1", "name": "Unconfigured", "operations": [] },
  "notification": null
}
```

`classification` is `unknown`, `nonproduction`, or `production`. Empty target
fields represent unresolved inputs, never a fabricated target. SQL endpoint
metadata is a hostname; REST endpoint metadata is an HTTPS base URL or explicit
loopback HTTP address for local development. Credential-bearing URLs are invalid.
No connection or secret lookup is performed during registration or preview.

A notification object has `template_asset_id` (nullable UUID), `sender` (string),
`recipients` (a bounded explicit list of mailbox strings), and `trusted_link`
(string). Empty values are unresolved. Template assets must be immutable ready
workspace assets. Registrations are visible only through the connection's
workspace/organization grants. Configuration withdrawal preserves its content
and history, but makes it unavailable for new/current approval.

## Preparation: `exercise-preparation/v1`

A board starts from one published revision, not the mutable scenario draft.
The immutable preview manifest pins that scenario snapshot, exact asset versions
and checksums, board draft, configuration snapshots, catalog versions, registered
operations, and the supplied target/notification/recovery information. The server
hashes canonical JSON (sorted object keys, compact UTF-8, finite numbers; ordered
arrays retain order) using SHA-256. The client never chooses the authoritative
digest.

The board draft describes explicit proposed steps independently of the authoring
diagram. A step has an ID, label, kind (`operation`, `condition`, `wait`),
optional authoring-node reference, configuration/operation/version binding,
typed scalar parameters, bounded waits, and explicit dependencies/branches.
Validate references and cycles across the complete graph, including conditional
branches. A condition compares a declared result from an earlier operation using
one of `eq`, `ne`, `gt`, `gte`, `lt`, or `lte`; it is not a code expression.

A parameter may instead bind a declared earlier result using
`{"source_step_id":"UUID","field":"result_name"}`. The source must be a guaranteed
predecessor, and its declared result type must match the destination parameter.
The UI distinguishes literals from result bindings; it must not require a user
to invent a request ID before the operation that creates it. These are declarative
references, not string interpolation, scripts, or calls during preview.

Missing target bindings, concrete record IDs, identity references, notification
inputs, recovery choices, or an explicit time window are readiness findings.
No target contents, permissions, connectivity, or evidence are inferred from
these descriptions. Production/unclassified targets cannot become execution
eligible. A valid static preview still reports unverified live readiness and
disabled execution.

Every material edit needs a fresh preview and approval. An approver must have
current workspace access and an explicit workspace approver grant, and cannot
be the board creator or any contributor to its preparation history. Ordinary
editor/owner/administrator status is not an approver grant.

An approval pins the preview, digest, reviewer, decision, explicit expiry, and
acknowledgement of unresolved prerequisites. Its kind is always `preparation`
and `execution_authorized` is always false. Read-time validity also checks
current authorization, configuration withdrawal, expiry and the board version.
It never changes the approved snapshot or promotes into execution authorization.

A future execution manifest must be a distinct strict type, with complete
bindings and approved concrete inputs, an explicit bounded window, and fresh
execution authorization. It cannot accept an unresolved preparation manifest.

## Product API surface

All paths below are relative to `/api/workspaces/{wid}`. They use ordinary Entra
authentication, workspace authorization, conditional mutations and correlated
audit. New request/response models are exported through `backend/openapi.json`;
generated TypeScript, not duplicate hand-maintained client shapes, is authoritative.

| Method     | Path                                                     | Purpose                                                                                    |
| ---------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| GET / POST | `/connections/{cid}/configurations`                      | List / register immutable configuration revisions; registration is organization-admin only |
| POST       | `/connections/{cid}/configurations/{config_id}/withdraw` | Withdraw configuration; organization-admin only                                            |
| GET / PUT  | `/approvers`                                             | List / grant explicit approver capability; organization-admin only for mutations           |
| DELETE     | `/approvers/{object_id}`                                 | Revoke explicit approver capability; organization-admin only                               |
| GET / POST | `/boards`                                                | List / create boards from an existing published scenario revision                          |
| GET / PUT  | `/boards/{bid}`                                          | Read / conditionally save preparation draft                                                |
| POST       | `/boards/{bid}/previews`                                 | Freeze a static preview of the exact saved board version                                   |
| GET        | `/boards/{bid}/previews`                                 | Read immutable preview history                                                             |
| POST       | `/boards/{bid}/approvals`                                | Record preparation approval/rejection of an exact preview                                  |
| GET        | `/boards/{bid}/approvals`                                | Read approval history and current validity                                                 |
| POST       | `/boards/{bid}/approvals/{approval_id}/revoke`           | Explicitly revoke a preparation approval                                                   |
| POST       | `/boards/{bid}/execute`                                  | Explicit execution-disabled error; no dispatch                                             |

Board creation body: `{"name":"...","scenario_id":"UUID","revision_version":1}`.
Approver grant body: `{"object_id":"UUID"}`. Board edits and preview/approval
requests require the board's current strong numeric `If-Match` ETag. Missing
preconditions receive 428, stale versions/digests receive 409, invalid input
receives 422, and unavailable workspace resources are not disclosed.

Approval request body has `preview_id`, `digest`, `decision` (`approved` or
`rejected`), `expires_at` (timezone-aware), `acknowledge_unverified` (must be true),
and `note`. The full board/preview/view shapes are generated from the backend
models. UI setup must never bypass these endpoints with special scenario seeds.

Collection reads return arrays without collection ETags. Registering an immutable
configuration and creating a new board append records rather than overwriting a
collection. Explicit approver grant/revoke targets the named identity's current
capability. These requests do not require a fabricated collection version.
Conditional writes apply to the board's saved version and to withdrawal of the
specific immutable configuration revision; clients must not mistake an absent
collection ETag for a failed read.

## External-system invariants

External systems own their domain schemas and separate API contract versions.
Game Theory does not import their code or recognize their operation names.

- A run ID or record ID is correlation, not authorization. Check the authenticated
  actor's grant for the run and operation before reads or writes.
- Idempotency is scoped to authenticated caller, run and operation. A repeated
  key/payload returns the original durable result; a changed payload receives
  conflict. Include concurrency preconditions in the payload fingerprint.
- Mutation, receipt and event evidence commit atomically in the target database.
  Replay checks precede new optimistic-concurrency checks so a post-commit retry
  can recover the original result. Competing requests cannot both mutate.
- Authoritative timestamps are UTC and include durable event IDs and opaque
  record versions. Inputs and responses explicitly distinguish succeeded,
  rejected, failed and unknown outcomes.
- Expected versions protect updates and recovery. Recovery previews are not a
  lock: apply rechecks ownership, versions, dependencies and authorization, keeps
  human changes, and returns per-record conflicts and partial results.
- Audit/assessment evidence is retained independently of eligible synthetic data
  cleanup. No cleanup operation claims to undo email.
- A deadline is inclusive: evidence committed exactly at the deadline is timely.
  Missing/incomplete observations cannot establish participant failure.
- Test clocks, test identities and seeded data must be labeled; fixtures cannot
  establish live target permissions, provider delivery or mailbox receipt.
