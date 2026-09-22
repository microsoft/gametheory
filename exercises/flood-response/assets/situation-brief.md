# Synthetic Riverwatch: shelter capacity and resource escalation

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
