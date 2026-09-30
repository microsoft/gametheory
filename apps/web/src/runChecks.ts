import type { components } from './api.generated'
import {
  describeGoal,
  describeUndo,
  describeWatch,
  emptySetup,
  importSetup,
  stepLabel,
  type RunSetupDraft,
  type SetupModel,
  type Trigger,
} from './runSetup'

type Schemas = components['schemas']
export type Suggestion = Schemas['RunCheckSuggestionView']
/** Form entries a suggestion filled: watch keys, measured objective IDs, and automatic undo steps. */
export type SuggestedEntries = { watches: string[]; objectives: string[]; undo: string[] }
/** The suggestion some current checks came from, recorded with the run as provenance only. */
export type SuggestionLink = SuggestedEntries & { id: string; preview_id: string }
export type SuggestedCheck = {
  key: string
  section: 'watch' | 'goal' | 'undo'
  title?: string
  text: string
  valid: boolean
  issues: string[]
}

export const MAX_WATCHES = 100
export const EVERY_GOAL_PROMPT =
  'Suggest checks for every goal in this scenario. Measure each goal from the declared results where you can, and ask me about any goal that cannot be measured.'

export function activeSuggestion(item: Suggestion) {
  return item.status === 'queued' || item.status === 'running'
}

/** Whether a suggestion can be used with this exact, current preview. */
export function currentFor(item: Suggestion, previewId: string | undefined) {
  return item.is_current && item.preview_id === previewId
}

export function usableCount(item: Suggestion) {
  return [...item.observations, ...item.objectives, ...item.recovery].filter((entry) => entry.valid)
    .length
}

/** Checks the operator would lose by replacing the forms; the trigger choice is always kept. */
export function hasChecks(draft: RunSetupDraft) {
  return entryCount(formEntries(draft)) > 0
}

export function formEntries(draft: RunSetupDraft): SuggestedEntries {
  return {
    watches: draft.watches.map((watch) => watch.key),
    objectives: draft.goals.filter((goal) => goal.measured).map((goal) => goal.objective_id),
    undo: draft.undo.filter((undo) => undo.mode === 'automatic').map((undo) => undo.step_id),
  }
}

export function entryCount(entries: SuggestedEntries) {
  return entries.watches.length + entries.objectives.length + entries.undo.length
}

/**
 * Keep a link only while at least one entry it filled is still in the forms. Entries count
 * while being edited, even when briefly incomplete: incomplete checks block creation anyway.
 */
export function remainingLink(link: SuggestionLink | undefined, draft: RunSetupDraft) {
  if (!link) return undefined
  const present = formEntries(draft)
  const remains = (['watches', 'objectives', 'undo'] as const).some((kind) =>
    link[kind].some((entry) => present[kind].includes(entry)),
  )
  return remains ? link : undefined
}

/** Link a suggestion that just filled entries, keeping what it filled earlier while linked. */
export function linkSuggestion(
  current: SuggestionLink | undefined,
  item: Pick<Suggestion, 'id' | 'preview_id'>,
  added: SuggestedEntries,
): SuggestionLink {
  const same = current?.id === item.id && current.preview_id === item.preview_id
  const union = (kind: keyof SuggestedEntries) => [
    ...new Set([...(same ? current[kind] : []), ...added[kind]]),
  ]
  return {
    id: item.id,
    preview_id: item.preview_id,
    watches: union('watches'),
    objectives: union('objectives'),
    undo: union('undo'),
  }
}

/** The valid items as a settings file, so they reach the forms through importSetup. */
export function suggestionSettings(item: Suggestion, trigger: Trigger): Record<string, unknown> {
  return {
    trigger,
    observations: item.observations.filter((entry) => entry.valid).map((entry) => entry.item),
    objectives: item.objectives.filter((entry) => entry.valid).map((entry) => entry.item),
    recovery: item.recovery.filter((entry) => entry.valid).map((entry) => entry.item),
  }
}

/** Fill only what the operator has not set: new read steps, unmeasured goals, manual undo. */
export function mergeSetup(
  current: RunSetupDraft,
  suggested: RunSetupDraft,
): { draft: RunSetupDraft; added: SuggestedEntries; skipped: number } {
  const watched = new Set(current.watches.map((watch) => watch.step_id))
  const candidates = suggested.watches.filter((watch) => !watched.has(watch.step_id))
  const room = Math.max(0, MAX_WATCHES - current.watches.length)
  const watches = candidates.slice(0, room)
  // Only a suggested rule replaces an entry, so unmeasured fields the operator typed are kept.
  const goal = (objectiveId: string) =>
    suggested.goals.find((item) => item.objective_id === objectiveId && item.measured)
  const undo = (stepId: string) =>
    suggested.undo.find((item) => item.step_id === stepId && item.mode === 'automatic')
  const goals = current.goals.map((item) =>
    item.measured ? item : (goal(item.objective_id) ?? item),
  )
  const undoPlan = current.undo.map((item) =>
    item.mode === 'automatic' ? item : (undo(item.step_id) ?? item),
  )
  return {
    draft: {
      trigger: current.trigger,
      watches: [...current.watches, ...watches],
      goals,
      undo: undoPlan,
    },
    added: {
      watches: watches.map((item) => item.key),
      objectives: current.goals
        .filter((item) => !item.measured && goal(item.objective_id))
        .map((item) => item.objective_id),
      undo: current.undo
        .filter((item) => item.mode !== 'automatic' && undo(item.step_id))
        .map((item) => item.step_id),
    },
    skipped: Math.max(0, candidates.length - room),
  }
}

export function applySuggestion(
  item: Suggestion,
  model: SetupModel,
  draft: RunSetupDraft,
  mode: 'replace' | 'merge',
): { draft: RunSetupDraft; added: SuggestedEntries; notes: string[] } {
  const imported = importSetup(suggestionSettings(item, draft.trigger), model, draft)
  if (mode === 'replace')
    return { draft: imported.draft, added: formEntries(imported.draft), notes: imported.problems }
  const merged = mergeSetup(draft, imported.draft)
  const notes = [...imported.problems]
  if (merged.skipped)
    notes.push(
      `${merged.skipped} suggested ${merged.skipped === 1 ? 'check was' : 'checks were'} not added because a run can have at most ${MAX_WATCHES} checks.`,
    )
  return { draft: merged.draft, added: merged.added, notes }
}

/** Read each suggested item back in the same plain language as the forms. */
export function describeSuggestion(model: SetupModel, item: Suggestion): SuggestedCheck[] {
  const base = emptySetup(model)
  const read = (value: Record<string, unknown>) => importSetup(value, model, base).draft
  const checks: SuggestedCheck[] = []
  item.observations.forEach((entry, index) => {
    const watch = read({ observations: [entry.item] }).watches[0]
    checks.push({
      key: `watch-${index}`,
      section: 'watch',
      text: watch ? describeWatch(model, watch) : 'This check does not match this preview.',
      valid: entry.valid,
      issues: entry.issues,
    })
  })
  item.objectives.forEach((entry, index) => {
    const id = entry.item.objective_id
    const goal = read({ objectives: [entry.item] }).goals.find((item) => item.objective_id === id)
    checks.push({
      key: `goal-${index}`,
      section: 'goal',
      title: model.objectives.find((objective) => objective.id === id)?.title ?? 'Unknown goal',
      text: goal?.measured ? describeGoal(model, goal) : 'This rule does not match this preview.',
      valid: entry.valid,
      issues: entry.issues,
    })
  })
  item.recovery.forEach((entry, index) => {
    const id = entry.item.step_id
    const undo = read({ recovery: [entry.item] }).undo.find((item) => item.step_id === id)
    checks.push({
      key: `undo-${index}`,
      section: 'undo',
      title: stepLabel(model, id),
      text:
        undo?.mode === 'automatic'
          ? describeUndo(model, undo)
          : 'This undo binding does not match this preview.',
      valid: entry.valid,
      issues: entry.issues,
    })
  })
  return checks
}
