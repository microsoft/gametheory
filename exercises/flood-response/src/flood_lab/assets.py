from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from flood_lab.openapi import artifact as openapi_artifact
from flood_lab.profile import PERSONNEL, PROFILE, SHELTERS, seed_manifest

ROOT = Path(__file__).resolve().parents[2]
ASSET_VERSION = "1.1.0"


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def field(name: str, kind: str, **constraints: Any) -> dict:
    return {"name": name, "type": kind, "required": True, **constraints}


def operation(
    key: str,
    label: str,
    effect: str,
    invocation: dict,
    parameters: list[dict],
    results: list[dict],
    recovery: str,
    *,
    version: str = "1",
) -> dict:
    return {
        "key": key,
        "version": version,
        "label": label,
        "effect": effect,
        "invocation": invocation,
        "parameters": parameters,
        "results": results,
        "recovery": recovery,
    }


def catalogs() -> dict[str, dict]:
    run = field("run_id", "uuid")
    shelter = field("shelter_id", "uuid")
    request = field("request_id", "uuid")
    idem = field("idempotency_key", "string", max_length=128)
    sql_expected = field("expected_version", "string", max_length=37)
    rest_expected = field("expected_version", "string", max_length=35)
    evidence = [
        field("outcome", "string", choices=["succeeded", "rejected", "failed", "unknown"]),
        field("durable_event_id", "uuid"),
        field("committed_at", "datetime"),
        field("correlation_id", "uuid"),
    ]
    occupancy = [
        run,
        shelter,
        field("occupancy", "integer", minimum=0, maximum=100000),
        field("capacity", "integer", minimum=1, maximum=100000),
        field("record_version", "string", max_length=37),
    ]
    request_result = [
        request,
        run,
        shelter,
        field("record_version", "string", max_length=35),
        field("status", "string", choices=["open", "acknowledged", "fulfilled"]),
        field("quantity_requested", "integer", minimum=1, maximum=10000),
        field("quantity_allocated", "integer", minimum=0, maximum=10000),
        field("created_at", "datetime"),
        field("needed_by", "datetime"),
    ]
    request_path = "/v1/runs/{run_id}/requests/{request_id}"
    recovery = (
        "No automatic undo. Reconcile unknown outcomes with the identical actor, run, operation, "
        "key, payload and expected version. Operator recovery retires only unchanged seed-owned "
        "records; participant changes, other operations and durable evidence are retained."
    )
    sql = [
        operation(
            "shelter.occupancy.read",
            "Read exercise shelter occupancy",
            "read",
            {"kind": "sql", "procedure": "flood.ReadOccupancy"},
            [run, shelter],
            [
                *occupancy,
                field("occupancy_percent", "number", minimum=0, maximum=100),
                field("durable_event_id", "uuid", required=False),
                field("committed_at", "datetime", required=False),
            ],
            "Read-only. SQL observer/injector role and a current SQL principal/run grant required. "
            "Compare occupancy_percent > 85 directly; no formula is required. "
            "Missing matching event ID or UTC timestamp means indeterminate timing, "
            "not an invented event or participant failure.",
            version="2",
        ),
        operation(
            "shelter.occupancy.update",
            "Inject synthetic occupancy",
            "write",
            {"kind": "sql", "procedure": "flood.UpdateOccupancy"},
            [
                run,
                shelter,
                field("occupancy", "integer", minimum=0, maximum=100000),
                sql_expected,
                idem,
            ],
            [
                *occupancy,
                field("occupancy_percent", "number", minimum=0, maximum=100, required=False),
                *evidence,
            ],
            recovery + " Injector role only; participant SQL access is absent. New receipts "
            "persist occupancy_percent; replayed pre-upgrade receipts may omit it. "
            "Never recompute a replay from current shelter state.",
            version="2",
        ),
    ]
    rest = [
        operation(
            "resource-request.create",
            "Create a synthetic resource request",
            "write",
            {"kind": "rest", "method": "POST", "path": "/v1/runs/{run_id}/requests"},
            [
                run,
                shelter,
                field(
                    "resource_type",
                    "string",
                    choices=["cots", "blankets", "water_cases", "transport_seats"],
                ),
                field("quantity_requested", "integer", minimum=1, maximum=10000),
                field("summary", "string", max_length=240),
                field("needed_by", "datetime"),
            ],
            [*request_result, *evidence],
            recovery + " Requires the service API role, not a participant. "
            "Idempotency-Key is generated by the caller/dispatcher, "
            "never a catalog-selected input.",
        ),
        operation(
            "resource-request.read",
            "Read a run-owned request",
            "read",
            {"kind": "rest", "method": "GET", "path": request_path},
            [run, request],
            request_result,
            "Read-only. The authenticated actor must hold an unexpired grant for the run.",
        ),
        operation(
            "resource-request.acknowledge",
            "Participant acknowledges ownership",
            "write",
            {"kind": "rest", "method": "POST", "path": request_path + "/acknowledge"},
            [run, request, rest_expected],
            [*request_result, *evidence],
            recovery + " Participant-only. The reserved expected_version value is unquoted; "
            "transport adds ETag quotes for If-Match and does not send it in JSON.",
        ),
        operation(
            "resource-request.allocate",
            "Participant records an allocation",
            "write",
            {"kind": "rest", "method": "POST", "path": request_path + "/allocate"},
            [
                run,
                request,
                rest_expected,
                field("quantity", "integer", minimum=1, maximum=10000),
                field("available_at", "datetime"),
            ],
            [*request_result, *evidence, field("allocation_id", "uuid")],
            recovery + " Participant-only. The reserved expected_version value is unquoted; "
            "transport adds ETag quotes for If-Match and does not send it in JSON.",
        ),
    ]
    graph = [
        operation(
            f"notification.{stage}",
            f"Describe {stage} exercise notification (sending disabled)",
            "notify",
            {"kind": "graph", "template_key": f"flood-{stage}-v1"},
            [run, request],
            [field("outcome", "string", choices=["succeeded", "rejected", "failed", "unknown"])],
            "DESCRIPTION ONLY. Graph sending is absent. Sender, recipient allowlist, immutable "
            "template and trusted link are unresolved. No delivery/acceptance is claimed. "
            "Email cannot be rolled back. One initial plus at most one escalation per request.",
        )
        for stage in ("initial", "escalation")
    ]
    return {
        kind: {
            "schema_version": "operation-catalog/v1",
            "name": f"Synthetic flood lab — {kind.upper()} descriptions",
            "operations": operations,
        }
        for kind, operations in (("sql", sql), ("rest", rest), ("graph", graph))
    }


BRIEF = """# Synthetic Riverwatch: shelter capacity and resource escalation

EXERCISE ONLY. All locations, personnel and operational requests are fictional.
This material is for preparation and a separately authorized nonproduction lab.
It is not an emergency directive or permission to contact a real service.

## Situation

Persistent synthetic rainfall has isolated neighborhoods along the fictional
Riverwatch tributary. Aster Reach School, Reedbank Community Hall and Willow
Bend Fieldhouse are receiving fictional evacuees. Coordinators use the
independent flood lab to acknowledge requests and record resource allocations.

## Objectives

- Detect occupancy strictly greater than 85 percent within 2 minutes of the
  injected occupancy event being committed.
- Acknowledge the resulting request within 10 minutes of its committed creation.
- Allocate the requested resource quantity within 20 minutes of committed
  request creation. A partial allocation alone does not meet this objective.
- Preserve durable event IDs, UTC timestamps and record versions as evidence.

## Roles

- Aster Vale is the fictional shelter coordinator.
- Rowan Finch is the fictional logistics coordinator.
- Maren Reed is the fictional exercise observer.
- Real lab permissions are separately granted to real Entra object IDs by the
  operator. These names, role descriptions and run IDs do not grant access.

## Exercise flow

1. The operator prepares an isolated run and the injector previews a change.
2. An explicitly authorized injector commits synthetic shelter occupancy.
3. Read the computed occupancy_percent and compare it to the literal 85 using
   greater-than. Exactly 85 is not a trigger; no formula or math DSL is needed.
   A missing matching durable event or UTC timestamp leaves timing indeterminate.
4. An authorized service creates a resource request in the independent lab.
5. A participant signs into the independent operations UI and acknowledges it.
6. If acknowledged within 10 minutes, continue directly to allocation review.
7. If no timely acknowledgement is evidenced, describe one escalation at most.
8. Review adequate quantity allocation against the 20-minute creation deadline.

## Notifications and boundaries

The initial and escalation templates are fixed exercise-only text. There is one
initial notification and at most one escalation, two messages total per request.
No sender, recipient set or trusted application link has been selected.
Graph sending is absent and disabled. No template or fixture is a sent message.
Any future real notification requires separate approval and cannot be recalled
by database recovery.

## Evidence and timing

The detection clock starts at the injected occupancy event's durable committed
UTC timestamp. Acknowledgement and allocation clocks start at the request's
committed created_at. Equality at a deadline is timely. Requested quantities
must be met by committed allocation events, not an entered availability time.
Missing, truncated or inconsistent observations produce indeterminate findings.
Test-only clocks and fixtures are never live evidence.

## Preparation in Game Theory

Use normal UI connection configuration and operation registration. Create a
blank scenario, upload the files, author this narrative and the objectives,
publish, create a board, explicitly bind operations, preview and request a
separate preparation review. Preparation approval is not execution approval.
No scenario installer or automatic Game Theory setup is supplied.
"""

CRITERIA = """# Demonstration objectives and evidence

EXERCISE ONLY. Profile demonstration/v1 is editable example policy, not live authorization.

## Capacity detection

Use the SQL read result occupancy_percent, a server-computed number from 0 to
100. In the generic board condition, choose that result, greater-than (gt), and
the numeric literal 85. No formula or math DSL is required. Exactly 85 percent
does not trigger. Start at the durable, committed occupancy-update event,
not the scheduled inject time, a polling timer or an uncommitted client timestamp.
The detection observation must identify that same run, shelter and source event.
Deadline: source event committed_at plus 120 seconds, inclusive. The read's
durable_event_id and committed_at outputs are optional: an out-of-band change
may leave no matching event. A known occupancy percentage without those fields
does not establish a start time; the timing finding remains indeterminate.

## Acknowledgement

Start at the resource request's committed created_at. Link the acknowledgement
event to the same run and request, and identify its authenticated participant.
Deadline: created_at plus 600 seconds, inclusive.

## Adequate allocation

Start at the same committed request created_at, not at acknowledgement. Sum
distinct succeeded allocation event quantities for that run/request/resource.
The sum must meet quantity_requested by created_at plus 1200 seconds, inclusive.
An entered available_at is a target time, not authoritative proof of allocation.
Do not double-count a receipt replay or duplicate durable event ID.

## Evidence completeness

Retain source events, request identity, record versions and UTC timestamps.
Missing or incomplete evidence is indeterminate, never an invented participant
failure. A late committed action is evidence of lateness; absence alone is not.
No participant grade is inferred from Graph templates, HTTP fixtures, preview
success, static registration or an absent notification provider.

## Branch and budget

There is one acknowledgement-versus-timeout branch. There are at most two
notification messages per request: one initial and one escalation. Never loop
escalation. Notification dispatch and provider observation are unimplemented.
Shorter clocks in tests are labeled TEST ONLY; this profile has no shortcuts.
"""

SETUP = """# UI-only preparation and independent lab operation

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
"""


def generated() -> dict[str, tuple[str, bytes]]:
    manifest = seed_manifest("demonstration-example")
    files: dict[str, tuple[str, bytes]] = {
        "flood-lab.openapi.json": ("application/json", openapi_artifact()),
        "profile.json": ("application/json", json_bytes(PROFILE)),
        "shelters.json": (
            "application/json",
            json_bytes({"synthetic": True, "shelters": SHELTERS}),
        ),
        "personnel.json": (
            "application/json",
            json_bytes({"synthetic": True, "personnel": PERSONNEL}),
        ),
        "seed-manifest-example.json": ("application/json", json_bytes(manifest)),
        "situation-brief.md": ("text/markdown", BRIEF.encode()),
        "objectives-and-evidence.md": ("text/markdown", CRITERIA.encode()),
        "operator-and-ui-setup.md": ("text/markdown", SETUP.encode()),
        "request-samples.json": (
            "application/json",
            json_bytes(
                {
                    "synthetic": True,
                    "label": (
                        "EXERCISE ONLY — substitute an authorized run shelter and future UTC time"
                    ),
                    "required_headers": ["Authorization", "Idempotency-Key"],
                    "requests": [
                        {
                            "shelter_id": manifest["shelters"][0]["id"],
                            "resource_type": "cots",
                            "quantity_requested": 24,
                            "summary": "EXERCISE ONLY — additional cots for Aster Reach School",
                            "needed_by": "2030-01-15T10:20:00Z",
                        }
                    ],
                }
            ),
        ),
    }
    schedule = io.StringIO(newline="")
    writer = csv.writer(schedule, lineterminator="\n")
    writer.writerow(["profile", "offset_seconds", "shelter_key", "operation", "occupancy", "label"])
    writer.writerow(
        [
            "demonstration/v1",
            0,
            "aster-reach",
            "shelter.occupancy.update",
            108,
            "EXERCISE ONLY; planned time is not evidence; capture committed event",
        ]
    )
    files["inject-schedule.csv"] = ("text/csv", schedule.getvalue().encode())
    for stage in ("initial", "escalation"):
        body = (
            f"Subject: EXERCISE ONLY — synthetic resource request ({stage})\n\n"
            "EXERCISE ONLY. This is fictional training material, not a real emergency.\n"
            f"Template: flood-{stage}-v1. Graph sending is absent and disabled.\n\n"
            "Run: {{run_id}}\nRequest: {{request_id}}\n"
            "Please review the synthetic request in the authorized operational application.\n"
            "Trusted operational link: {{trusted_link}}\n\n"
            "Sender and recipient allowlist remain unresolved until explicitly configured.\n"
            "Budget: one initial notification and at most one escalation; two total.\n"
            "Do not initiate real-world dispatch or infer receipt from this template.\n"
        )
        files[f"notification-{stage}.txt"] = ("text/plain", body.encode())
    for kind, catalog in catalogs().items():
        files[f"operation-catalog-{kind}.json"] = ("application/json", json_bytes(catalog))
    checksums = {
        "schema_version": "flood-lab-assets/v1",
        "asset_version": ASSET_VERSION,
        "profile_version": PROFILE["profile_version"],
        "synthetic": True,
        "files": [
            {
                "path": name,
                "version": ASSET_VERSION,
                "media_type": media,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for name, (media, content) in sorted(files.items())
        ],
    }
    files["manifest.json"] = ("application/json", json_bytes(checksums))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate only independent synthetic lab assets")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    target = ROOT / "assets"
    drift = []
    for name, (_, content) in generated().items():
        path = target / name
        if args.check:
            if not path.exists() or path.read_bytes() != content:
                drift.append(name)
        else:
            target.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if drift:
        print("Asset drift: " + ", ".join(drift))
        return 1
    print(f"{'Checked' if args.check else 'Generated'} {len(generated())} independent lab assets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
