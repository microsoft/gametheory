# Demonstration objectives and evidence

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
