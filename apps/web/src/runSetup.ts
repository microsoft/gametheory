import type { components } from './api.generated'
import type { OperationField, RegisteredOperation } from './Catalog'
import { scalarError, type ScalarValue } from './ScalarParameters'

type Schemas = components['schemas']
export type Preview = Schemas['PreparationPreviewView']
export type PinnedStep = Schemas['PreparationStep']
export type PinnedConfiguration = Schemas['ConfigurationSnapshot']
export type Objective = Schemas['Objective']
export type Comparator = Schemas['Observation']['operator']
export type Observation = Schemas['Observation']
export type ObjectiveRule = Schemas['ObjectiveRule']
export type RecoveryBinding = Schemas['RecoveryBinding']
export type ResultReference = Schemas['PriorResultReference']
export type Trigger = 'manual' | 'scheduled'
export type RunBindings = {
  trigger: Trigger
  observations: Observation[]
  objectives: ObjectiveRule[]
  recovery: RecoveryBinding[]
}

export type BoundStep = {
  step: PinnedStep
  configuration: PinnedConfiguration
  operation: RegisteredOperation
  conditional: boolean
}
export type RecoveryOption = {
  key: string
  configuration: PinnedConfiguration
  operation: RegisteredOperation
}
export type SetupModel = {
  bound: BoundStep[]
  byId: Map<string, BoundStep>
  reads: BoundStep[]
  writes: BoundStep[]
  objectives: Objective[]
  recoveryOptions: RecoveryOption[]
  window: { starts_at: string; ends_at: string } | null
  windowSeconds: number | null
  idempotencyLiterals: BoundStep[]
}

export type ValueInput =
  | { source: 'literal'; value: ScalarValue | undefined }
  | { source: 'result'; source_step_id: string; field: string }
export type WatchInput = {
  key: string
  step_id: string
  field: string
  operator: Comparator
  value: ScalarValue | undefined
  interval_seconds: number | undefined
  timeout_seconds: number | undefined
  max_samples: number | undefined
}
export type GoalInput = {
  objective_id: string
  measured: boolean
  step_id: string
  field: string
  operator: Comparator
  value: ValueInput
  timed: boolean
  anchor_step_id: string
  anchor_field: string
  within_seconds: number | undefined
  clock: 'observed' | 'source'
  source_time_field: string
}
export type UndoInput = {
  step_id: string
  mode: 'manual' | 'automatic'
  operation_key: string
  parameters: Record<string, ValueInput>
  ownership_parameter: string
  version_parameter: string
}
export type RunSetupDraft = {
  trigger: Trigger
  watches: WatchInput[]
  goals: GoalInput[]
  undo: UndoInput[]
}
export type SetupSection = 'start' | 'watch' | 'goal' | 'undo' | 'preparation'
export type SetupProblem = { section: SetupSection; key: string; message: string }
export type SetupOrigins = { observations: string[]; objectives: string[]; recovery: string[] }

export const comparators: ReadonlyArray<readonly [Comparator, string]> = [
  ['eq', 'is equal to'],
  ['ne', 'is not equal to'],
  ['gt', 'is greater than'],
  ['gte', 'is at least'],
  ['lt', 'is less than'],
  ['lte', 'is at most'],
]
const ordered = new Set(['integer', 'number', 'datetime'])
export const DEFAULT_INTERVAL_SECONDS = 10
export const DEFAULT_TIMEOUT_SECONDS = 600
export const DEFAULT_MAX_SAMPLES = 60
export const MAX_ATTEMPTS = 1000

export function supportsOrdering(field: OperationField | undefined) {
  return !!field && ordered.has(field.type)
}

export function comparatorLabel(operator: Comparator) {
  return comparators.find(([key]) => key === operator)?.[1] ?? operator
}

export function recoveryKey(configurationId: string, operation: RegisteredOperation) {
  return `${configurationId}/${operation.key}/${operation.version}`
}

export function isDispatcherOwned(operation: RegisteredOperation, field: OperationField) {
  return operation.invocation.kind === 'sql' && field.name === 'idempotency_key'
}

function conditionalSteps(steps: PinnedStep[]): Set<string> {
  const next = new Map<string, string[]>()
  const link = (from: string, to: string) => next.set(from, [...(next.get(from) ?? []), to])
  const targets: string[] = []
  for (const step of steps) {
    for (const dependency of step.depends_on ?? []) link(dependency, step.id)
    for (const target of [
      ...(step.condition?.if_true ?? []),
      ...(step.condition?.if_false ?? []),
    ]) {
      link(step.id, target)
      targets.push(target)
    }
  }
  const seen = new Set(targets)
  const queue = [...targets]
  while (queue.length) {
    for (const following of next.get(queue.shift()!) ?? []) {
      if (!seen.has(following)) {
        seen.add(following)
        queue.push(following)
      }
    }
  }
  return seen
}

export function setupModel(preview: Preview): SetupModel {
  const manifest = preview.manifest
  const steps = manifest.draft.steps ?? []
  const conditional = conditionalSteps(steps)
  const bound: BoundStep[] = []
  for (const step of steps) {
    const binding = step.binding
    if (step.kind !== 'operation' || !binding) continue
    const configuration = manifest.configurations.find(
      (item) => item.id === binding.configuration_id,
    )
    const operation = configuration?.content.catalog.operations?.find(
      (item) => item.key === binding.operation_key && item.version === binding.operation_version,
    )
    if (configuration && operation)
      bound.push({ step, configuration, operation, conditional: conditional.has(step.id) })
  }
  const window = manifest.draft.window ?? null
  const span = window ? (Date.parse(window.ends_at) - Date.parse(window.starts_at)) / 1000 : NaN
  return {
    bound,
    byId: new Map(bound.map((item) => [item.step.id, item])),
    reads: bound.filter((item) => item.operation.effect === 'read'),
    writes: bound.filter((item) => item.operation.effect === 'write'),
    objectives: manifest.scenario.content.objectives ?? [],
    recoveryOptions: manifest.configurations.flatMap((configuration) =>
      (configuration.content.catalog.operations ?? [])
        .filter((operation) => operation.effect === 'write')
        .map((operation) => ({
          key: recoveryKey(configuration.id, operation),
          configuration,
          operation,
        })),
    ),
    window,
    windowSeconds: Number.isFinite(span) && span > 0 ? span : null,
    idempotencyLiterals: bound.filter(
      ({ step, operation }) =>
        operation.invocation.kind === 'sql' &&
        step.parameters?.idempotency_key !== undefined &&
        step.parameters.idempotency_key !== null,
    ),
  }
}

export function resultField(model: SetupModel, stepId: string, name: string) {
  return model.byId.get(stepId)?.operation.results?.find((field) => field.name === name)
}

export function stepLabel(model: SetupModel, stepId: string) {
  return model.byId.get(stepId)?.step.label || 'an unavailable step'
}

export function newWatch(model: SetupModel, draft: RunSetupDraft): WatchInput {
  const watched = new Set(draft.watches.map((watch) => watch.step_id))
  return {
    key: crypto.randomUUID(),
    step_id: model.reads.find((read) => !watched.has(read.step.id))?.step.id ?? '',
    field: '',
    operator: 'eq',
    value: undefined,
    interval_seconds: DEFAULT_INTERVAL_SECONDS,
    timeout_seconds:
      model.windowSeconds === null
        ? DEFAULT_TIMEOUT_SECONDS
        : Math.min(DEFAULT_TIMEOUT_SECONDS, Math.floor(model.windowSeconds)),
    max_samples: DEFAULT_MAX_SAMPLES,
  }
}

export function newGoal(objectiveId: string): GoalInput {
  return {
    objective_id: objectiveId,
    measured: false,
    step_id: '',
    field: '',
    operator: 'eq',
    value: { source: 'literal', value: undefined },
    timed: false,
    anchor_step_id: '',
    anchor_field: '',
    within_seconds: undefined,
    clock: 'observed',
    source_time_field: '',
  }
}

export function newUndo(stepId: string): UndoInput {
  return {
    step_id: stepId,
    mode: 'manual',
    operation_key: '',
    parameters: {},
    ownership_parameter: '',
    version_parameter: '',
  }
}

export function emptySetup(model: SetupModel): RunSetupDraft {
  return {
    trigger: 'manual',
    watches: [],
    goals: model.objectives.map((objective) => newGoal(objective.id)),
    undo: model.writes.map((write) => newUndo(write.step.id)),
  }
}

/** Keep entries whose pinned references still exist; add defaults for new goals and writes. */
export function alignSetup(draft: RunSetupDraft, model: SetupModel): RunSetupDraft {
  return {
    trigger: draft.trigger,
    watches: draft.watches,
    goals: model.objectives.map(
      (objective) =>
        draft.goals.find((goal) => goal.objective_id === objective.id) ?? newGoal(objective.id),
    ),
    undo: model.writes.map(
      (write) =>
        draft.undo.find((item) => item.step_id === write.step.id) ?? newUndo(write.step.id),
    ),
  }
}

/** Suggest a mapping by matching names; the operator reviews every value before creation. */
export function suggestedUndo(model: SetupModel, current: UndoInput, key: string): UndoInput {
  const option = model.recoveryOptions.find((item) => item.key === key)
  const source = model.byId.get(current.step_id)
  if (!option || !source) return { ...current, operation_key: key, parameters: {} }
  const recorded = (source.operation.results ?? []).filter((field) => field.required !== false)
  const parameters: Record<string, ValueInput> = {}
  for (const field of option.operation.parameters ?? []) {
    if (isDispatcherOwned(option.operation, field)) continue
    const names = field.name === 'expected_version' ? ['record_version', field.name] : [field.name]
    const match = names
      .map((name) => recorded.find((result) => result.name === name && result.type === field.type))
      .find(Boolean)
    parameters[field.name] = match
      ? { source: 'result', source_step_id: current.step_id, field: match.name }
      : { source: 'literal', value: undefined }
  }
  const owned = Object.entries(parameters).filter(
    ([, value]) => value.source === 'result' && value.source_step_id === current.step_id,
  )
  const version = owned.find(([name]) => name === 'expected_version')?.[0] ?? ''
  return {
    ...current,
    operation_key: key,
    parameters,
    version_parameter: version,
    ownership_parameter: owned.find(([name]) => name !== version)?.[0] ?? '',
  }
}

function typeOnly(field: OperationField): OperationField {
  return { ...field, minimum: null, maximum: null, max_length: null, choices: null }
}

function resolvedValue(value: ValueInput): ScalarValue | ResultReference | undefined {
  if (value.source === 'literal') return value.value
  return value.source_step_id && value.field
    ? { source_step_id: value.source_step_id, field: value.field }
    : undefined
}

export function setupBindings(
  draft: RunSetupDraft,
  model: SetupModel,
): { bindings: RunBindings; origins: SetupOrigins } {
  const origins: SetupOrigins = { observations: [], objectives: [], recovery: [] }
  const observations: Observation[] = []
  for (const watch of draft.watches) {
    if (!watch.step_id || !watch.field || watch.value === undefined) continue
    observations.push({
      step_id: watch.step_id,
      field: watch.field,
      operator: watch.operator,
      value: watch.value,
      interval_seconds: watch.interval_seconds ?? DEFAULT_INTERVAL_SECONDS,
      timeout_seconds: watch.timeout_seconds ?? DEFAULT_TIMEOUT_SECONDS,
      max_samples: watch.max_samples ?? DEFAULT_MAX_SAMPLES,
    })
    origins.observations.push(watch.key)
  }
  const objectives: ObjectiveRule[] = []
  for (const goal of draft.goals) {
    const value = resolvedValue(goal.value)
    if (!goal.measured || !goal.step_id || !goal.field || value === undefined) continue
    const rule: ObjectiveRule = {
      objective_id: goal.objective_id,
      step_id: goal.step_id,
      field: goal.field,
      operator: goal.operator,
      value,
    }
    if (goal.timed) {
      rule.anchor_step_id = goal.anchor_step_id || null
      rule.anchor_field = goal.anchor_field || null
      rule.within_seconds = goal.within_seconds ?? null
      if (goal.clock === 'source') rule.source_time_field = goal.source_time_field || null
    }
    objectives.push(rule)
    origins.objectives.push(goal.objective_id)
  }
  const recovery: RecoveryBinding[] = []
  for (const undo of draft.undo) {
    const option = model.recoveryOptions.find((item) => item.key === undo.operation_key)
    if (undo.mode !== 'automatic' || !option) continue
    const parameters: RecoveryBinding['parameters'] = {}
    for (const [name, input] of Object.entries(undo.parameters)) {
      const value = resolvedValue(input)
      if (value !== undefined) parameters[name] = value
    }
    recovery.push({
      step_id: undo.step_id,
      binding: {
        configuration_id: option.configuration.id,
        operation_key: option.operation.key,
        operation_version: option.operation.version,
      },
      parameters,
      ownership_parameter: undo.ownership_parameter,
      version_parameter: undo.version_parameter,
    })
    origins.recovery.push(undo.step_id)
  }
  return { bindings: { trigger: draft.trigger, observations, objectives, recovery }, origins }
}

export function plannedAttempts(draft: RunSetupDraft, model: SetupModel) {
  const sampled = new Map(
    draft.watches
      .filter((watch) => watch.step_id)
      .map((watch) => [watch.step_id, watch.max_samples ?? DEFAULT_MAX_SAMPLES]),
  )
  return (
    model.bound.reduce((sum, item) => sum + (sampled.get(item.step.id) ?? 1), 0) +
    draft.undo.filter((undo) => undo.mode === 'automatic').length
  )
}

function inRange(value: number | undefined, minimum: number, maximum: number) {
  return value !== undefined && Number.isSafeInteger(value) && value >= minimum && value <= maximum
}

export function setupProblems(draft: RunSetupDraft, model: SetupModel): SetupProblem[] {
  const problems: SetupProblem[] = []
  const add = (section: SetupSection, key: string, message: string) =>
    problems.push({ section, key, message })
  for (const item of model.idempotencyLiterals)
    add(
      'preparation',
      item.step.id,
      `“${item.step.label}” has a typed idempotency_key. Runs supply this key automatically, so clear the value in the Preparation tab, save, and freeze a new preview.`,
    )
  const watched = new Set<string>()
  for (const watch of draft.watches) {
    const read = model.byId.get(watch.step_id)
    const field = read?.operation.results?.find((item) => item.name === watch.field)
    if (!read || read.operation.effect !== 'read') {
      add('watch', watch.key, 'Choose a read step to check.')
      continue
    }
    if (watched.has(watch.step_id))
      add('watch', watch.key, 'Each read step can have only one check. Remove the duplicate.')
    watched.add(watch.step_id)
    if (!field) add('watch', watch.key, 'Choose a reading declared by this step.')
    else {
      if (!supportsOrdering(field) && !['eq', 'ne'].includes(watch.operator))
        add('watch', watch.key, `A ${field.type} reading can only be equal or not equal.`)
      const error = scalarError(typeOnly(field), watch.value)
      if (watch.value === undefined) add('watch', watch.key, 'Enter the value to compare with.')
      else if (error) add('watch', watch.key, error)
    }
    if (!inRange(watch.interval_seconds, 1, 3600))
      add('watch', watch.key, 'Check at least once an hour and no more than once a second.')
    if (!inRange(watch.timeout_seconds, 1, 86400))
      add('watch', watch.key, 'Give up after a time between 1 second and 24 hours.')
    else if (model.windowSeconds !== null && watch.timeout_seconds! > model.windowSeconds)
      add('watch', watch.key, 'The give-up time must fit inside the exercise window.')
    if (!inRange(watch.max_samples, 1, 1000))
      add('watch', watch.key, 'Allow between 1 and 1,000 checks.')
  }
  for (const goal of draft.goals) {
    if (!goal.measured) continue
    const key = goal.objective_id
    const compared = resultField(model, goal.step_id, goal.field)
    if (!model.byId.has(goal.step_id)) add('goal', key, 'Choose the step that provides evidence.')
    else if (!compared) add('goal', key, 'Choose a result declared by that step.')
    if (compared && !supportsOrdering(compared) && !['eq', 'ne'].includes(goal.operator))
      add('goal', key, `A ${compared.type} result can only be equal or not equal.`)
    if (goal.value.source === 'literal') {
      if (goal.value.value === undefined) add('goal', key, 'Enter the value that counts as met.')
      else if (compared) {
        const error = scalarError(typeOnly(compared), goal.value.value)
        if (error) add('goal', key, error)
      }
    } else {
      const other = resultField(model, goal.value.source_step_id, goal.value.field)
      if (!other) add('goal', key, 'Choose the earlier result to compare with.')
      else if (compared && other.type !== compared.type)
        add('goal', key, `Compare with another ${compared.type} result.`)
    }
    if (goal.timed) {
      const anchor = resultField(model, goal.anchor_step_id, goal.anchor_field)
      if (!anchor || anchor.type !== 'datetime')
        add('goal', key, 'Choose the recorded time the deadline starts from.')
      if (!inRange(goal.within_seconds, 1, 604800))
        add('goal', key, 'Set a time limit between 1 second and 7 days.')
      if (goal.clock === 'source') {
        const clock = resultField(model, goal.step_id, goal.source_time_field)
        if (!clock || clock.type !== 'datetime')
          add('goal', key, 'Choose the time the system recorded for this evidence.')
      }
    }
  }
  for (const undo of draft.undo) {
    if (undo.mode !== 'automatic') continue
    const option = model.recoveryOptions.find((item) => item.key === undo.operation_key)
    if (!option) {
      add('undo', undo.step_id, 'Choose the registered operation that undoes this change.')
      continue
    }
    for (const field of option.operation.parameters ?? []) {
      if (isDispatcherOwned(option.operation, field)) continue
      const input = undo.parameters[field.name]
      const label = `${field.name}: `
      if (!input || (input.source === 'literal' && input.value === undefined)) {
        if (field.required !== false) add('undo', undo.step_id, `${label}supply a value.`)
        continue
      }
      if (input.source === 'literal') {
        const error = scalarError(
          field,
          input.value,
          option.operation.invocation.kind === 'rest' && field.name === 'expected_version',
        )
        if (error) add('undo', undo.step_id, label + error)
      } else {
        const recorded = resultField(model, input.source_step_id, input.field)
        if (!recorded || recorded.required === false || recorded.type !== field.type)
          add(
            'undo',
            undo.step_id,
            `${label}choose a required recorded ${field.type} result from the run.`,
          )
      }
    }
    const owned = (name: string) => {
      const input = undo.parameters[name]
      return input?.source === 'result' && input.source_step_id === undo.step_id
    }
    if (!undo.ownership_parameter || !owned(undo.ownership_parameter))
      add('undo', undo.step_id, 'Choose an ownership check that uses a value this change recorded.')
    if (!undo.version_parameter || !owned(undo.version_parameter))
      add('undo', undo.step_id, 'Choose a version check that uses a value this change recorded.')
    if (undo.ownership_parameter && undo.ownership_parameter === undo.version_parameter)
      add('undo', undo.step_id, 'Ownership and version checks must use different inputs.')
  }
  return problems
}

function duration(seconds: number | undefined) {
  if (seconds === undefined || !Number.isFinite(seconds)) return 'an unset time'
  const units: Array<[number, string]> = [
    [3600, 'hour'],
    [60, 'minute'],
    [1, 'second'],
  ]
  const [size, unit] = units.find(([size]) => seconds % size === 0) ?? [1, 'second']
  const amount = seconds / size
  return `${amount.toLocaleString()} ${unit}${amount === 1 ? '' : 's'}`
}

export function describeValue(model: SetupModel, value: ValueInput) {
  if (value.source === 'result')
    return value.field
      ? `the ${value.field} recorded by “${stepLabel(model, value.source_step_id)}”`
      : 'an earlier result'
  return value.value === undefined ? 'a value' : JSON.stringify(value.value)
}

export function describeWatch(model: SetupModel, watch: WatchInput) {
  if (!watch.step_id) return 'Choose a read step to begin.'
  const reading = watch.field || 'a reading'
  const value = watch.value === undefined ? 'a value' : JSON.stringify(watch.value)
  return `Every ${duration(watch.interval_seconds)}, read “${stepLabel(model, watch.step_id)}” until ${reading} ${comparatorLabel(watch.operator)} ${value}. Stop after ${(watch.max_samples ?? 0).toLocaleString()} checks or ${duration(watch.timeout_seconds)}, whichever comes first.`
}

export function describeGoal(model: SetupModel, goal: GoalInput) {
  if (!goal.measured) return 'Not measured automatically. This goal will show as indeterminate.'
  if (!goal.step_id) return 'Choose the step that provides evidence.'
  let text = `Met when “${stepLabel(model, goal.step_id)}” shows ${goal.field || 'a result'} ${comparatorLabel(goal.operator)} ${describeValue(model, goal.value)}`
  if (goal.timed)
    text += ` within ${duration(goal.within_seconds)} of the ${goal.anchor_field || 'recorded time'} from “${stepLabel(model, goal.anchor_step_id)}”, timed by ${goal.clock === 'source' ? `the system's own ${goal.source_time_field || 'timestamp'}` : 'when Game Theory observed it'}`
  return `${text}. Missing evidence stays indeterminate.`
}

export function describeUndo(model: SetupModel, undo: UndoInput) {
  if (undo.mode === 'manual')
    return 'An operator accounts for this change in the external system and records a report.'
  const option = model.recoveryOptions.find((item) => item.key === undo.operation_key)
  if (!option) return 'Choose the registered operation that undoes this change.'
  return `After the exercise, run “${option.operation.label}” only if ${undo.ownership_parameter || 'the ownership check'} and ${undo.version_parameter || 'the version check'} still match what this change recorded. Human edits are preserved.`
}

function scalar(value: unknown): value is ScalarValue {
  return (
    typeof value === 'string' ||
    typeof value === 'boolean' ||
    (typeof value === 'number' && Number.isFinite(value))
  )
}

function reference(value: unknown): ResultReference | undefined {
  if (typeof value !== 'object' || value === null) return undefined
  const entries = value as Record<string, unknown>
  return typeof entries.source_step_id === 'string' && typeof entries.field === 'string'
    ? { source_step_id: entries.source_step_id, field: entries.field }
    : undefined
}

function valueInput(value: unknown): ValueInput | undefined {
  if (scalar(value)) return { source: 'literal', value }
  const found = reference(value)
  return found ? { source: 'result', ...found } : undefined
}

function comparator(value: unknown): Comparator | undefined {
  return comparators.find(([key]) => key === value)?.[0]
}

function optionalInteger(value: unknown) {
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : undefined
}

function text(value: unknown) {
  return typeof value === 'string' ? value : ''
}

/** Map a strict settings file onto the forms. The service still validates every value. */
export function importSetup(
  value: Record<string, unknown>,
  model: SetupModel,
  base: RunSetupDraft,
): { draft: RunSetupDraft; problems: string[] } {
  const problems: string[] = []
  const list = (name: string) => {
    const entries = value[name]
    return Array.isArray(entries) ? entries : []
  }
  const objectEntries = (name: string) =>
    list(name).filter((entry, index): entry is Record<string, unknown> => {
      const valid = typeof entry === 'object' && entry !== null && !Array.isArray(entry)
      if (!valid) problems.push(`${name} item ${index + 1} is not an object and was skipped.`)
      return valid
    })
  const watches: WatchInput[] = []
  for (const entry of objectEntries('observations')) {
    const operator = comparator(entry.operator)
    if (!operator || !scalar(entry.value)) {
      problems.push('An observation without a valid comparison was skipped.')
      continue
    }
    watches.push({
      key: crypto.randomUUID(),
      step_id: text(entry.step_id),
      field: text(entry.field),
      operator,
      value: entry.value,
      interval_seconds: optionalInteger(entry.interval_seconds) ?? DEFAULT_INTERVAL_SECONDS,
      timeout_seconds: optionalInteger(entry.timeout_seconds) ?? DEFAULT_TIMEOUT_SECONDS,
      max_samples: optionalInteger(entry.max_samples) ?? DEFAULT_MAX_SAMPLES,
    })
  }
  const goals = base.goals.map((goal) => newGoal(goal.objective_id))
  for (const entry of objectEntries('objectives')) {
    const index = goals.findIndex((goal) => goal.objective_id === entry.objective_id)
    const operator = comparator(entry.operator)
    const compared = valueInput(entry.value)
    if (index < 0 || !operator || !compared) {
      problems.push(
        index < 0
          ? 'A rule for a goal that is not in this preview was skipped.'
          : 'A goal rule without a valid comparison was skipped.',
      )
      continue
    }
    const anchor = text(entry.anchor_step_id)
    goals[index] = {
      ...goals[index],
      measured: true,
      step_id: text(entry.step_id),
      field: text(entry.field),
      operator,
      value: compared,
      timed: !!anchor,
      anchor_step_id: anchor,
      anchor_field: text(entry.anchor_field),
      within_seconds: optionalInteger(entry.within_seconds),
      clock: entry.source_time_field ? 'source' : 'observed',
      source_time_field: text(entry.source_time_field),
    }
  }
  const undo = base.undo.map((item) => newUndo(item.step_id))
  for (const entry of objectEntries('recovery')) {
    const index = undo.findIndex((item) => item.step_id === entry.step_id)
    const binding = entry.binding as Record<string, unknown> | undefined
    const option = model.recoveryOptions.find(
      (item) =>
        item.configuration.id === binding?.configuration_id &&
        item.operation.key === binding?.operation_key &&
        item.operation.version === binding?.operation_version,
    )
    if (index < 0 || !option) {
      problems.push(
        index < 0
          ? 'A recovery binding for a change that is not in this preview was skipped.'
          : 'A recovery binding for an unregistered operation was skipped.',
      )
      continue
    }
    const parameters: Record<string, ValueInput> = {}
    const raw = entry.parameters
    if (typeof raw === 'object' && raw !== null)
      for (const [name, item] of Object.entries(raw)) {
        const input = valueInput(item)
        if (input) parameters[name] = input
        else problems.push(`Recovery input ${name} was not a value or recorded result.`)
      }
    undo[index] = {
      step_id: undo[index].step_id,
      mode: 'automatic',
      operation_key: option.key,
      parameters,
      ownership_parameter: text(entry.ownership_parameter),
      version_parameter: text(entry.version_parameter),
    }
  }
  const trigger = value.trigger === 'scheduled' ? 'scheduled' : base.trigger
  return { draft: { trigger, watches, goals, undo }, problems }
}
