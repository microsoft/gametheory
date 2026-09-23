import { describe, expect, it } from 'vitest'
import golden from '../../../backend/tests/fixtures/guided-run-setup.json'
import { runOptions } from './ExerciseRuns'
import {
  alignSetup,
  describeGoal,
  describeUndo,
  describeWatch,
  emptySetup,
  importSetup,
  newWatch,
  plannedAttempts,
  setupBindings,
  setupModel,
  setupProblems,
  suggestedUndo,
  type Preview,
  type RunSetupDraft,
  type SetupModel,
} from './runSetup'

const manifest = golden.preview_manifest as unknown as Preview['manifest']
const ids = {
  raise: manifest.draft.steps![0].id,
  readOccupancy: manifest.draft.steps![1].id,
  openTicket: manifest.draft.steps![2].id,
  readTicket: manifest.draft.steps![3].id,
  breach: manifest.scenario.content.objectives![0].id,
  acknowledged: manifest.scenario.content.objectives![1].id,
  rest: manifest.configurations[1].id,
}

function preview(changes: Partial<Preview['manifest']> = {}): Preview {
  return {
    id: 'preview',
    board_id: manifest.board_id,
    board_version: manifest.board_version,
    sequence: 1,
    digest: 'e'.repeat(64),
    manifest: { ...structuredClone(manifest), ...changes },
    findings: [],
    created_by: 'author',
    created_at: '2026-09-22T12:00:00Z',
    is_current: true,
    execution_authorized: false,
    execution_eligible: false,
  }
}

/** The choices an operator makes in the guided form for the golden scenario. */
function operatorChoices(model: SetupModel): RunSetupDraft {
  const draft = emptySetup(model)
  const watch = newWatch(model, draft)
  draft.watches = [{ ...watch, field: 'occupancy_percent', operator: 'gt', value: 85 }]
  draft.goals = draft.goals.map((goal) =>
    goal.objective_id === ids.breach
      ? {
          ...goal,
          measured: true,
          step_id: ids.readOccupancy,
          field: 'occupancy_percent',
          operator: 'gt',
          value: { source: 'literal', value: 85 },
          timed: true,
          within_seconds: 120,
          anchor_step_id: ids.raise,
          anchor_field: 'committed_at',
        }
      : {
          ...goal,
          measured: true,
          step_id: ids.readTicket,
          field: 'acknowledged',
          operator: 'eq',
          value: { source: 'literal', value: true },
          timed: true,
          within_seconds: 600,
          anchor_step_id: ids.openTicket,
          anchor_field: 'created_at',
          clock: 'source',
          source_time_field: 'acknowledged_at',
        },
  )
  draft.undo = draft.undo.map((undo) =>
    undo.step_id === ids.openTicket
      ? suggestedUndo(model, { ...undo, mode: 'automatic' }, `${ids.rest}/ticket.close/1`)
      : undo,
  )
  return draft
}

describe('guided run setup model', () => {
  it('offers only pinned read steps, goals, writes, and registered undo operations', () => {
    const model = setupModel(preview())
    expect(model.reads.map((item) => item.step.label)).toEqual(['Read occupancy', 'Read ticket'])
    expect(model.writes.map((item) => item.step.label)).toEqual([
      'Raise occupancy',
      'Open resource ticket',
    ])
    expect(model.objectives.map((item) => item.title)).toEqual([
      'Breach identified quickly',
      'Ticket acknowledged',
    ])
    expect(model.recoveryOptions.map((item) => item.operation.key)).toEqual([
      'capacity.update',
      'ticket.create',
      'ticket.close',
    ])
    expect(model.windowSeconds).toBe(7200)
    expect(model.idempotencyLiterals).toEqual([])
    const draft = emptySetup(model)
    expect(draft.goals.every((goal) => !goal.measured)).toBe(true)
    expect(draft.undo.every((undo) => undo.mode === 'manual')).toBe(true)
    expect(setupProblems(draft, model)).toEqual([])
    expect(setupBindings(draft, model).bindings).toEqual({
      trigger: 'manual',
      observations: [],
      objectives: [],
      recovery: [],
    })
  })

  it('turns form choices into exactly the bindings the service validated', () => {
    const model = setupModel(preview())
    const draft = operatorChoices(model)
    expect(setupProblems(draft, model)).toEqual([])
    const { bindings, origins } = setupBindings(draft, model)
    expect(bindings).toEqual(golden.run_create)
    expect(origins.objectives).toEqual([ids.breach, ids.acknowledged])
    expect(origins.recovery).toEqual([ids.openTicket])
    expect(plannedAttempts(draft, model)).toBe(1 + 60 + 1 + 1 + 1)
  })

  it('reads back each choice in plain language', () => {
    const model = setupModel(preview())
    const draft = operatorChoices(model)
    expect(describeWatch(model, draft.watches[0])).toBe(
      'Every 10 seconds, read “Read occupancy” until occupancy_percent is greater than 85. Stop after 60 checks or 10 minutes, whichever comes first.',
    )
    expect(describeGoal(model, draft.goals[1])).toBe(
      "Met when “Read ticket” shows acknowledged is equal to true within 10 minutes of the created_at from “Open resource ticket”, timed by the system's own acknowledged_at. Missing evidence stays indeterminate.",
    )
    expect(describeGoal(model, emptySetup(model).goals[0])).toMatch(/indeterminate/)
    expect(describeUndo(model, draft.undo[1])).toMatch(
      /run “Close an exercise ticket” only if record_id and expected_version still match/,
    )
    expect(describeUndo(model, draft.undo[0])).toMatch(/accounts for this change/)
  })

  it('reports incomplete or inconsistent choices before the service is asked', () => {
    const model = setupModel(preview())
    const draft = operatorChoices(model)
    draft.watches = [
      { ...draft.watches[0], operator: 'gt', value: undefined, timeout_seconds: 9000 },
      { ...draft.watches[0], key: 'duplicate' },
    ]
    draft.goals[0] = { ...draft.goals[0], field: 'invented', operator: 'eq', timed: false }
    draft.goals[1] = { ...draft.goals[1], operator: 'gt', within_seconds: undefined }
    draft.undo[1] = { ...draft.undo[1], version_parameter: draft.undo[1].ownership_parameter }
    const messages = setupProblems(draft, model).map((item) => `${item.section}: ${item.message}`)
    expect(messages).toEqual(
      expect.arrayContaining([
        'watch: Enter the value to compare with.',
        'watch: The give-up time must fit inside the exercise window.',
        'watch: Each read step can have only one check. Remove the duplicate.',
        'goal: Choose a result declared by that step.',
        'goal: A boolean result can only be equal or not equal.',
        'goal: Set a time limit between 1 second and 7 days.',
        'undo: Ownership and version checks must use different inputs.',
      ]),
    )
  })

  it('flags a typed SQL idempotency key so the preparation can be fixed first', () => {
    const steps = structuredClone(manifest.draft.steps!)
    steps[0].parameters = { ...steps[0].parameters, idempotency_key: 'typed-by-hand' }
    const model = setupModel(preview({ draft: { ...manifest.draft, steps } }))
    expect(model.idempotencyLiterals.map((item) => item.step.label)).toEqual(['Raise occupancy'])
    expect(setupProblems(emptySetup(model), model)).toEqual([
      expect.objectContaining({ section: 'preparation', key: ids.raise }),
    ])
  })

  it('imports its own settings file back into the same forms', () => {
    const model = setupModel(preview())
    const exported = setupBindings(operatorChoices(model), model).bindings
    const text = JSON.stringify(exported)
    const imported = importSetup(runOptions(text), model, emptySetup(model))
    expect(imported.problems).toEqual([])
    expect(setupBindings(imported.draft, model).bindings).toEqual(exported)
  })

  it('skips imported rules that do not belong to this preview and says so', () => {
    const model = setupModel(preview())
    const { draft, problems } = importSetup(
      {
        objectives: [
          {
            objective_id: 'elsewhere',
            step_id: ids.readTicket,
            field: 'x',
            operator: 'eq',
            value: 1,
          },
        ],
        recovery: [{ step_id: ids.readTicket, binding: {}, parameters: {} }],
        observations: ['not an object'],
      },
      model,
      emptySetup(model),
    )
    expect(problems).toHaveLength(3)
    expect(draft.goals.every((goal) => !goal.measured)).toBe(true)
    expect(draft.watches).toEqual([])
  })

  it('keeps choices for references that still exist when the preview changes', () => {
    const model = setupModel(preview())
    const draft = operatorChoices(model)
    const objectives = [manifest.scenario.content.objectives![1]]
    const narrowed = setupModel(
      preview({
        scenario: { ...manifest.scenario, content: { ...manifest.scenario.content, objectives } },
      }),
    )
    const aligned = alignSetup(draft, narrowed)
    expect(aligned.goals).toEqual([draft.goals[1]])
    expect(aligned.watches).toEqual(draft.watches)
    expect(aligned.undo).toEqual(draft.undo)
  })

  it('accepts only a trigger and binding arrays in a settings file', () => {
    expect(runOptions('{"trigger":"scheduled","observations":[]}')).toEqual({
      trigger: 'scheduled',
      observations: [],
    })
    expect(() => runOptions('{"trigger":"now"}')).toThrow()
    expect(() => runOptions('{"preview_id":"swap"}')).toThrow()
  })
})
