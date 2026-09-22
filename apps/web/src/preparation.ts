import type { components } from './api.generated'
import type { RegisteredOperation } from './Catalog'
import { scalarError } from './ScalarParameters'

type Schemas = components['schemas']
export type BoardDraft = Schemas['BoardDraft']
export type PreparationStep = Schemas['PreparationStep']
export type BoardSummary = Schemas['BoardSummary']
export type BoardView = Schemas['BoardView']
export type Preview = Schemas['PreparationPreviewView']
export type Approval = Schemas['PreparationApprovalView']
export type ApprovalInput = Schemas['PreparationApprovalInput']
export type Configuration = Schemas['ConfigurationView']
export type ApproverGrant = Schemas['ApproverGrantView']
export type Me = Schemas['MeView']
export type Binding = NonNullable<PreparationStep['binding']>
export type ParameterValue = NonNullable<PreparationStep['parameters']>[string]
export type ResultReference = Extract<ParameterValue, object>

export function isResultReference(value: ParameterValue | undefined): value is ResultReference {
  return typeof value === 'object' && value !== null
}

export function parameterValue(step: PreparationStep, name: string): ParameterValue | undefined {
  return step.parameters && Object.hasOwn(step.parameters, name) ? step.parameters[name] : undefined
}

export function preparationReason(reason: string): string {
  return (
    new Map([
      ['explicit_approver_grant_required', 'An explicit workspace approver grant is required.'],
      [
        'creator_or_contributor',
        'Board creators and preparation contributors cannot review this board.',
      ],
      [
        'preview_reference_unavailable',
        'A connection grant changed. Preview content is unavailable; repair the draft bindings before requesting a new preview.',
      ],
      [
        'configuration_withdrawn',
        'A bound configuration was withdrawn. Select an active revision, save, and freeze a new preview.',
      ],
    ]).get(reason) ?? reason.replaceAll('_', ' ')
  )
}

export function bindingKey(binding: Binding | null | undefined) {
  return binding
    ? `${binding.configuration_id}/${binding.operation_key}/${binding.operation_version}`
    : ''
}

export function boundOperation(
  step: PreparationStep,
  configurations: Configuration[],
): { configuration: Configuration; operation: RegisteredOperation } | undefined {
  const binding = step.binding
  if (!binding) return undefined
  const configuration = configurations.find((item) => item.id === binding.configuration_id)
  const operation = configuration?.content.catalog.operations?.find(
    (item) => item.key === binding.operation_key && item.version === binding.operation_version,
  )
  return configuration && operation ? { configuration, operation } : undefined
}

export function newPreparationStep(kind: PreparationStep['kind']): PreparationStep {
  return {
    id: crypto.randomUUID(),
    label: '',
    kind,
    authoring_node_id: null,
    depends_on: [],
    binding: null,
    parameters: {},
    wait_seconds: null,
    condition: null,
  }
}

export function localDateTime(value: string | undefined | null) {
  if (!value) return ''
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const offset = date.getTimezoneOffset() * 60000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

export function timezoneDateTime(value: string) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toISOString() : value
}

export function draftErrors(draft: BoardDraft, configurations: Configuration[]): string[] {
  const errors: string[] = []
  if (!draft.name.trim()) errors.push('Give this board a name.')
  const steps = draft.steps ?? []
  const ids = new Set(steps.map((step) => step.id))
  const edges = new Map<string, Set<string>>(steps.map((step) => [step.id, new Set()]))
  if (ids.size !== steps.length) errors.push('Step identifiers must be unique.')
  if (steps.length > 200) errors.push('A preparation can contain at most 200 steps.')
  if (steps.reduce((sum, step) => sum + (step.wait_seconds ?? 0), 0) > 7 * 86400)
    errors.push('Total proposed waits cannot exceed seven days.')
  if (
    draft.notification_budget != null &&
    (!Number.isSafeInteger(draft.notification_budget) ||
      draft.notification_budget < 0 ||
      draft.notification_budget > 100)
  )
    errors.push('Notification budget must be a whole number from 0 to 100.')
  if (draft.window) {
    const starts = Date.parse(draft.window.starts_at)
    const ends = Date.parse(draft.window.ends_at)
    if (!Number.isFinite(starts) || !Number.isFinite(ends) || ends <= starts)
      errors.push('Supply a complete time window whose end is later than its start.')
    else if (ends - starts > 7 * 86400000)
      errors.push('The proposed time window cannot exceed seven days.')
  }
  for (const step of steps) {
    const label = step.label.trim() || `Step ${steps.indexOf(step) + 1}`
    if (!step.label.trim()) errors.push(`${label}: give the step a label.`)
    for (const dependency of step.depends_on ?? []) {
      if (!ids.has(dependency)) errors.push(`${label}: a dependency is no longer available.`)
      else edges.get(dependency)?.add(step.id)
    }
    if (step.kind === 'wait') {
      if (
        step.wait_seconds == null ||
        !Number.isSafeInteger(step.wait_seconds) ||
        step.wait_seconds < 1 ||
        step.wait_seconds > 86400
      )
        errors.push(`${label}: supply a bounded wait from 1 to 86,400 seconds.`)
    }
    if (step.kind === 'operation') {
      const binding = boundOperation(step, configurations)
      if (step.binding && !binding)
        errors.push(
          `${label}: the referenced operation is not available in the loaded configuration list. Reload access or select an available binding.`,
        )
      if (binding) {
        for (const field of binding.operation.parameters ?? []) {
          const value = parameterValue(step, field.name)
          if (isResultReference(value)) {
            const source = steps.find(
              (candidate) =>
                candidate.id === value.source_step_id && candidate.kind === 'operation',
            )
            const output =
              source &&
              boundOperation(source, configurations)?.operation.results?.find(
                (result) => result.name === value.field,
              )
            if (!source || !output)
              errors.push(`${label} / ${field.name}: select a declared prior operation result.`)
            else if (output.required === false)
              errors.push(
                `${label} / ${field.name}: optional outputs cannot supply a result reference.`,
              )
            else if (output.type !== field.type)
              errors.push(
                `${label} / ${field.name}: the prior result must match the parameter's ${field.type} type.`,
              )
          } else {
            const error = scalarError(
              field,
              value ?? undefined,
              binding.operation.invocation.kind === 'rest' && field.name === 'expected_version',
            )
            if (error) errors.push(`${label} / ${field.name}: ${error}`)
          }
        }
      }
    }
    if (step.kind === 'condition') {
      const condition = step.condition
      if (!condition) {
        errors.push(`${label}: select a declared result to compare.`)
        continue
      }
      const source = steps.find((item) => item.id === condition.source_step_id)
      if (!source || source.kind !== 'operation')
        errors.push(`${label}: choose an operation as the condition source.`)
      const result =
        source &&
        boundOperation(source, configurations)?.operation.results?.find(
          (field) => field.name === condition.result_field,
        )
      if (!result) errors.push(`${label}: choose a declared result from the source operation.`)
      else {
        if (result.required === false)
          errors.push(
            `${label}: conditions require a declared, required result, not an optional output.`,
          )
        const error = scalarError(result, condition.value)
        if (error) errors.push(`${label} comparison: ${error}`)
        if (
          !['number', 'integer', 'datetime'].includes(result.type) &&
          !['eq', 'ne'].includes(condition.operator)
        )
          errors.push(`${label}: this result type supports equality comparisons only.`)
      }
      if (condition.value === '') errors.push(`${label}: supply an explicit comparison value.`)
      const branches = [...(condition.if_true ?? []), ...(condition.if_false ?? [])]
      if (!branches.length || new Set(branches).size !== branches.length)
        errors.push(
          `${label}: choose at least one branch target; true and false targets must be distinct.`,
        )
      for (const target of branches) {
        if (!ids.has(target)) errors.push(`${label}: a branch target is no longer available.`)
        else edges.get(step.id)?.add(target)
      }
      if ([...edges.values()].reduce((sum, targets) => sum + targets.size, 0) > 1000)
        errors.push('A preparation can contain at most 1,000 dependency and branch edges.')
    }
  }
  const visited = new Set<string>()
  const active = new Set<string>()
  function cycle(id: string): boolean {
    if (active.has(id)) return true
    if (visited.has(id)) return false
    active.add(id)
    for (const target of edges.get(id) ?? []) if (cycle(target)) return true
    active.delete(id)
    visited.add(id)
    return false
  }
  const cyclic = steps.some((step) => cycle(step.id))
  if (cyclic)
    errors.push(
      'The preparation graph contains a cycle. Remove a dependency or branch before saving.',
    )
  if (!cyclic && ids.size === steps.length) errors.push(...guaranteedSourceErrors(steps, edges))
  return [...new Set(errors)]
}

function guaranteedSourceErrors(
  steps: PreparationStep[],
  edges: Map<string, Set<string>>,
): string[] {
  const errors: string[] = []
  const byId = new Map(steps.map((step) => [step.id, step]))
  const controls = new Map<string, Array<{ parent: string; outcome: boolean }>>()
  const indegree = new Map(steps.map((step) => [step.id, 0]))
  for (const targets of edges.values())
    for (const target of targets) indegree.set(target, (indegree.get(target) ?? 0) + 1)
  for (const step of steps) {
    if (!step.condition) continue
    for (const [outcome, targets] of [
      [true, step.condition.if_true ?? []],
      [false, step.condition.if_false ?? []],
    ] as const) {
      for (const target of targets)
        controls.set(target, [...(controls.get(target) ?? []), { parent: step.id, outcome }])
    }
  }
  const guaranteed = new Map<string, Set<string>>()
  const guards = new Map<string, Map<string, boolean>>()
  const pending = steps.filter((step) => indegree.get(step.id) === 0).map((step) => step.id)
  while (pending.length) {
    const id = pending.shift()!
    const step = byId.get(id)!
    const before = new Set<string>()
    const requiredGuards = new Map<string, boolean>()
    for (const dependency of step.depends_on ?? []) {
      if (!byId.has(dependency)) continue
      before.add(dependency)
      for (const predecessor of guaranteed.get(dependency) ?? []) before.add(predecessor)
      for (const [condition, outcome] of guards.get(dependency) ?? []) {
        if (requiredGuards.has(condition) && requiredGuards.get(condition) !== outcome)
          errors.push(`${step.label}: dependencies cannot require mutually exclusive branches.`)
        requiredGuards.set(condition, outcome)
      }
    }
    const entries = controls.get(id) ?? []
    if (entries.length) {
      const alternatives = entries.flatMap(({ parent, outcome }) => {
        const branchGuards = new Map(guards.get(parent))
        branchGuards.set(parent, outcome)
        if (
          [...branchGuards].some(
            ([condition, value]) =>
              requiredGuards.has(condition) && requiredGuards.get(condition) !== value,
          )
        )
          return []
        return [
          { before: new Set([...(guaranteed.get(parent) ?? []), parent]), guards: branchGuards },
        ]
      })
      if (!alternatives.length) {
        errors.push(`${step.label}: a branch cannot depend on its mutually exclusive sibling.`)
      } else {
        const common = alternatives[0]
        for (const alternative of alternatives.slice(1)) {
          for (const predecessor of common.before)
            if (!alternative.before.has(predecessor)) common.before.delete(predecessor)
          for (const [condition, outcome] of common.guards)
            if (alternative.guards.get(condition) !== outcome) common.guards.delete(condition)
        }
        for (const predecessor of common.before) before.add(predecessor)
        for (const [condition, outcome] of common.guards) requiredGuards.set(condition, outcome)
      }
    }
    guaranteed.set(id, before)
    guards.set(id, requiredGuards)
    for (const target of edges.get(id) ?? []) {
      const count = (indegree.get(target) ?? 0) - 1
      indegree.set(target, count)
      if (count === 0) pending.push(target)
    }
  }
  for (const step of steps) {
    const sources = Object.values(step.parameters ?? {})
      .filter(isResultReference)
      .map((value) => value.source_step_id)
    if (step.condition) sources.push(step.condition.source_step_id)
    for (const source of sources) {
      if (byId.get(source)?.kind !== 'operation')
        errors.push(`${step.label}: a result source must be an operation in this preparation.`)
      else if (!guaranteed.get(step.id)?.has(source))
        errors.push(
          `${step.label}: the result source must be a guaranteed predecessor, not merely a branch sibling.`,
        )
    }
  }
  return errors
}
