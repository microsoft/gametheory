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
Deadline: created_at plus 600 seconds, inclusive. Bind the milestone read's
acknowledged_on_time, equals (eq), true. The equivalent source-time form binds
acknowledged eq true, anchored on that read's created_at within 600 seconds,
with source time field acknowledged_at.

## Adequate allocation

Start at the same committed request created_at, not at acknowledgement. Sum
distinct succeeded allocation event quantities for that run/request/resource.
The sum must meet quantity_requested by created_at plus 1200 seconds, inclusive.
An entered available_at is a target time, not authoritative proof of allocation.
Do not double-count a receipt replay or duplicate durable event ID. Bind the
milestone read's allocated_on_time, equals (eq), true.

## Authoritative milestone read

REST operation resource-request.milestones version 1 reads one request's
bounded timing in a single consistent lab read. Enter 600 for
acknowledge_within_seconds and 1200 for allocate_within_seconds; both accept 1
to 604800. The lab takes every clock value from its own database: the committed
request.create, request.acknowledge and request.allocate events, and as_of for
the read itself. The read waits for in-flight lab writers, so no later commit
can carry an earlier time. Each durable event ID counts once.

- true: the milestone committed at or before its inclusive deadline.
- false: a late committed action, or a committed partial allocation still short
  after the deadline with complete lab history.
- null: undecided, inconsistent or absent. A request never acknowledged stays
  null after its deadline. Seeded requests have no request.create event, so
  created_event_id and both verdicts stay null.

A decided verdict never changes. A late observation of an on-time event is still
on time; a fulfilled status seen late is not proof of timely fulfilment.

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
