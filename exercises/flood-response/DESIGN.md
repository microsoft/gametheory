# Independent operations UI

The brief pins a sober operational design. This record describes the built
`ui/src/styles.css` and `ui/src/App.tsx`, not a replacement Game Theory brand.

## Structure

An explicit exercise-only notice precedes a compact product header. The
authenticated surface has a run selector, request list and task-detail split
view. At 800px it becomes a single column; the request list scrolls independently.
Setup, signed-out, loading, empty, observer-only, conflicting and unknown-result
states are first-class. There are no participant reset or notification actions.

## Visual system

System UI sans carries labels, body and data. Body text is 0.95rem with 1.6 line
height; headings use fixed 1.05–1.5rem steps. The light scene is a lit exercise
room: mineral `#edf4f5` ground, white task surfaces, dark blue-gray `#223d48` ink,
ocean `#09656b` controls. System dark mode switches to `#0f1c28` / `#1a2b3b` with
`#e0edf4` ink and `#8cdde1` controls.

Borders separate records and sections instead of nested cards. Buttons and
fields share compact 5–6px corners, 42–44px minimum heights and an explicit
three-pixel keyboard focus outline. Status always includes text, never color
alone. Error notices use the semantic danger palette.

## Interaction and accessibility

Native labeled inputs, selects, buttons, details and lists provide ordinary
keyboard behavior. A skip link targets the operations region. Save results and
errors announce through status/alert roles. Conflicts preserve entered values.
Unknown writes keep an account-scoped journal and block unrelated mutations
until reconciliation. No decorative motion, external fonts, imagery or telemetry.

## Verification boundary

Component and test-only browser checks validate task states and responsive
structure, not live Entra access, SQL connectivity, participant performance or
message delivery. All notification sending remains absent and visibly disabled.
