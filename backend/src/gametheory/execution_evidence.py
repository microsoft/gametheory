import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import TypeAdapter

from gametheory.execution import ObjectiveFinding, RunManifest, compare
from gametheory.persistence import RunEvent
from gametheory.preparation import PriorResultReference, Scalar

RESULTS = TypeAdapter(dict[str, Scalar | None])


def evidence_time(value: Scalar | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def findings(manifest: RunManifest, events: list[RunEvent]) -> list[ObjectiveFinding]:
    samples: dict[str, list[tuple[RunEvent, dict[str, Scalar | None]]]] = {}
    for event in events:
        if event.kind not in {"operation.succeeded", "observation"} or event.step_id is None:
            continue
        detail = json.loads(event.detail)
        if detail.get("phase") != "exercise":
            continue
        values = RESULTS.validate_json(detail["result"])
        samples.setdefault(event.step_id, []).append((event, values))
    bound = {rule.objective_id: rule for rule in manifest.objectives}
    result = []
    for objective in manifest.preparation.scenario.content.objectives:
        finding = ObjectiveFinding(
            objective_id=objective.id,
            state="indeterminate",
            reason="No executable evidence rule has been bound to this objective.",
            evidence_ids=[],
        )
        result.append(finding)
        rule = bound.get(objective.id)
        if rule is None:
            continue
        observed = samples.get(str(rule.step_id), [])
        if not observed:
            finding.reason = "No successful observation evidence is available."
            continue
        right: Scalar | None
        if isinstance(rule.value, PriorResultReference):
            source = samples.get(str(rule.value.source_step_id), [])
            right = source[0][1].get(rule.value.field) if source else None
        else:
            right = rule.value
        anchor: datetime | None = None
        if rule.anchor_step_id:
            source = samples.get(str(rule.anchor_step_id), [])
            anchor = evidence_time(source[0][1].get(rule.anchor_field or "")) if source else None
            if source:
                finding.evidence_ids.append(UUID(source[0][0].id))
            if anchor is None:
                finding.reason = "The authoritative start timestamp is missing."
                continue
        if right is None:
            finding.reason = "A required comparison result is missing."
            continue
        for event, values in observed:
            left = values.get(rule.field)
            if left is None:
                finding.reason = "A declared optional evidence field is missing."
                continue
            try:
                satisfied = compare(left, rule.operator, right)
            except ValueError:
                finding.reason = "Evidence types are inconsistent."
                continue
            finding.evidence_ids.append(UUID(event.id))
            if anchor is None:
                finding.state = "met" if satisfied else "unmet"
                finding.reason = (
                    "The recorded observation meets the predicate."
                    if satisfied
                    else "The recorded observation does not meet the predicate."
                )
                if satisfied:
                    break
                continue
            observed_at = event.created_at.replace(tzinfo=UTC)
            measured = (
                evidence_time(values.get(rule.source_time_field))
                if rule.source_time_field
                else observed_at
            )
            if measured is None or measured < anchor or measured > observed_at:
                finding.reason = "Source timing is absent or inconsistent with observation."
                continue
            deadline = anchor + timedelta(seconds=rule.within_seconds or 0)
            if satisfied and measured <= deadline:
                finding.state, finding.reason = (
                    "met",
                    "Evidence meets the predicate at or before the inclusive deadline.",
                )
                break
            if satisfied and rule.source_time_field and measured > deadline:
                finding.state, finding.reason = (
                    "unmet",
                    "The authoritative source event occurred after the deadline.",
                )
            else:
                finding.reason = "Point observations do not prove complete coverage of the deadline; the outcome remains indeterminate."
        finding.evidence_ids = list(dict.fromkeys(finding.evidence_ids))
    return result
