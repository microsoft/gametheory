import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { useState } from 'react'
import golden from '../../../backend/tests/fixtures/guided-run-setup.json'
import { createApi, SessionContext } from './api'
import { BoardRuns, emptyRunSetup, type RunSetupInput } from './ExerciseRuns'
import {
  applySuggestion,
  describeSuggestion,
  EVERY_GOAL_PROMPT,
  hasChecks,
  linkSuggestion,
  mergeSetup,
  remainingLink,
  suggestionSettings,
  type Suggestion,
} from './runChecks'
import {
  describeWatch,
  emptySetup,
  importSetup,
  newWatch,
  setupBindings,
  setupModel,
  type Preview,
} from './runSetup'
import type { Config } from './types'

const manifest = golden.preview_manifest as unknown as Preview['manifest']
const ids = {
  readOccupancy: manifest.draft.steps![1].id,
  openTicket: manifest.draft.steps![2].id,
  breach: manifest.scenario.content.objectives![0].id,
  acknowledged: manifest.scenario.content.objectives![1].id,
}
const bindings = golden.run_create as unknown as {
  observations: Suggestion['observations'][number]['item'][]
  objectives: Suggestion['objectives'][number]['item'][]
  recovery: Suggestion['recovery'][number]['item'][]
}
const config = (run_assistant: boolean): Config => ({
  cloud: 'commercial',
  auth: { configured: true, client_id: 'fixture', authority: 'https://fixture.invalid', scope: '' },
  capabilities: { authoring: true, assets: false, planning: true, execution: true, run_assistant },
  max_upload_bytes: 1024,
})

function preview(): Preview {
  return {
    id: 'golden-preview',
    board_id: manifest.board_id,
    board_version: 3,
    sequence: 2,
    digest: 'e'.repeat(64),
    manifest: structuredClone(manifest),
    findings: [],
    created_by: 'author',
    created_at: '2026-09-22T12:00:00Z',
    is_current: true,
    execution_authorized: false,
    execution_eligible: false,
  }
}

function valid<T>(item: T) {
  return { item, valid: true, issues: [] as string[] }
}

function suggestion(changes: Partial<Suggestion> = {}): Suggestion {
  return {
    id: 'suggestion-1',
    preview_id: 'golden-preview',
    prompt: 'Make sure they acknowledge within 10 minutes and watch occupancy above 85%.',
    status: 'proposed',
    error: null,
    created_at: '2026-09-23T12:00:00Z',
    summary: 'Watch occupancy, then judge both goals from recorded results.',
    observations: bindings.observations.map(valid),
    objectives: bindings.objectives.map(valid),
    recovery: bindings.recovery.map(valid),
    questions: ['Which result shows the cots arrived?'],
    is_current: true,
    ...changes,
  }
}

describe('mapping reviewed suggestions into the guided forms', () => {
  const model = setupModel(preview())

  it('fills the forms with exactly the bindings the service validated', () => {
    const { draft, notes } = applySuggestion(suggestion(), model, emptySetup(model), 'replace')
    expect(notes).toEqual([])
    expect(setupBindings(draft, model).bindings).toEqual(golden.run_create)
  })

  it('uses only valid items and keeps the operator’s start choice', () => {
    const invalid = {
      item: { ...bindings.observations[0], field: 'invented' },
      valid: false,
      issues: ['Observation must compare a declared result'],
    }
    const item = suggestion({ observations: [invalid], recovery: [] })
    expect(suggestionSettings(item, 'scheduled')).toEqual({
      trigger: 'scheduled',
      observations: [],
      objectives: bindings.objectives,
      recovery: [],
    })
    const scheduled = { ...emptySetup(model), trigger: 'scheduled' as const }
    const { draft } = applySuggestion(item, model, scheduled, 'replace')
    expect(draft.trigger).toBe('scheduled')
    expect(draft.watches).toEqual([])
    expect(draft.goals.every((goal) => goal.measured)).toBe(true)
  })

  it('adds to existing checks without overwriting what the operator set', () => {
    const current = emptySetup(model)
    const mine = { ...newWatch(model, current), field: 'occupancy_percent', value: 90 }
    current.watches = [mine]
    current.goals = current.goals.map((goal) =>
      goal.objective_id === ids.breach
        ? {
            ...goal,
            measured: true,
            step_id: ids.readOccupancy,
            field: 'occupancy_percent',
            operator: 'gte' as const,
            value: { source: 'literal' as const, value: 95 },
          }
        : goal,
    )
    const suggested = importSetup(suggestionSettings(suggestion(), 'manual'), model, current).draft
    const { draft, added, skipped } = mergeSetup(current, suggested)
    expect(skipped).toBe(0)
    expect(draft.watches).toEqual([mine])
    expect(draft.goals.find((goal) => goal.objective_id === ids.breach)).toEqual(current.goals[0])
    expect(draft.goals.find((goal) => goal.objective_id === ids.acknowledged)?.measured).toBe(true)
    expect(draft.undo.find((undo) => undo.step_id === ids.openTicket)?.mode).toBe('automatic')
    expect(added).toEqual({ watches: [], objectives: [ids.acknowledged], undo: [ids.openTicket] })
    expect(hasChecks(emptySetup(model))).toBe(false)
    expect(hasChecks(draft)).toBe(true)
  })

  it('adds nothing, and says so, when the forms already cover every suggested item', () => {
    const applied = applySuggestion(suggestion(), model, emptySetup(model), 'replace')
    expect(applied.added).toEqual({
      watches: [applied.draft.watches[0].key],
      objectives: [ids.breach, ids.acknowledged],
      undo: [ids.openTicket],
    })
    const again = applySuggestion(suggestion(), model, applied.draft, 'merge')
    expect(again.added).toEqual({ watches: [], objectives: [], undo: [] })
    expect(again.draft).toEqual(applied.draft)
  })

  it('keeps what the operator typed on a goal the suggestion does not cover', () => {
    const current = emptySetup(model)
    current.goals = current.goals.map((goal) =>
      goal.objective_id === ids.breach ? { ...goal, step_id: ids.readOccupancy } : goal,
    )
    const partial = suggestion({ objectives: [valid(bindings.objectives[1])] })
    const { draft, added } = applySuggestion(partial, model, current, 'merge')
    expect(draft.goals.find((goal) => goal.objective_id === ids.breach)).toEqual(current.goals[0])
    expect(added.objectives).toEqual([ids.acknowledged])
  })

  it('keeps a link while any entry it filled remains, even mid-edit, and drops it after', () => {
    const { draft, added } = applySuggestion(suggestion(), model, emptySetup(model), 'replace')
    const link = linkSuggestion(undefined, suggestion(), added)
    const edited = {
      ...draft,
      watches: draft.watches.map((watch) => ({ ...watch, value: undefined })),
      goals: draft.goals.map((goal) => ({ ...goal, measured: false })),
    }
    expect(remainingLink(link, edited)).toBe(link)
    const cleared = {
      ...edited,
      watches: [],
      undo: edited.undo.map((undo) => ({ ...undo, mode: 'manual' as const })),
    }
    expect(remainingLink(link, cleared)).toBeUndefined()
    const mine = { ...cleared, watches: [newWatch(model, cleared)] }
    expect(remainingLink(link, mine)).toBeUndefined()
    expect(remainingLink(undefined, draft)).toBeUndefined()
  })

  it('adds to what a suggestion filled earlier, and a different suggestion replaces it', () => {
    const first = linkSuggestion(undefined, suggestion(), {
      watches: ['watch-a'],
      objectives: [ids.breach],
      undo: [],
    })
    const more = { watches: ['watch-b'], objectives: [ids.breach], undo: [ids.openTicket] }
    expect(linkSuggestion(first, suggestion(), more)).toEqual({
      id: 'suggestion-1',
      preview_id: 'golden-preview',
      watches: ['watch-a', 'watch-b'],
      objectives: [ids.breach],
      undo: [ids.openTicket],
    })
    expect(linkSuggestion(first, suggestion({ id: 'suggestion-2' }), more)).toEqual({
      id: 'suggestion-2',
      preview_id: 'golden-preview',
      ...more,
    })
  })

  it('reads each suggestion back in the same plain language as the forms', () => {
    const checks = describeSuggestion(model, suggestion())
    expect(checks.map((check) => check.section)).toEqual(['watch', 'goal', 'goal', 'undo'])
    const { draft } = applySuggestion(suggestion(), model, emptySetup(model), 'replace')
    expect(checks[0].text).toBe(describeWatch(model, draft.watches[0]))
    expect(checks[2]).toMatchObject({ title: 'Ticket acknowledged', valid: true })
    expect(checks[2].text).toMatch(/within 10 minutes of the created_at/)
    expect(checks[3].text).toMatch(/run “Close an exercise ticket” only if record_id/)
  })
})

function mount(run_assistant = true) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Harness() {
    const [input, setInput] = useState<RunSetupInput>(emptyRunSetup)
    return (
      <>
        <BoardRuns
          wid="workspace"
          bid="board"
          version={3}
          preview={preview()}
          unsaved={false}
          input={input}
          onInputChange={setInput}
          onOpenPreparation={() => undefined}
        />
        <output aria-label="Run setup dirty">{String(input.dirty)}</output>
      </>
    )
  }
  const router = createMemoryRouter([{ path: '*', element: <Harness /> }])
  render(
    <QueryClientProvider client={cache}>
      <SessionContext.Provider
        value={{ api: createApi(async () => 'test-only'), config: config(run_assistant) }}
      >
        <RouterProvider router={router} />
      </SessionContext.Provider>
    </QueryClientProvider>,
  )
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

function fixture(history: () => Response) {
  const fetch = vi.fn(async (url: string, options: RequestInit = {}) => {
    if (url.endsWith('/me')) return json({ object_id: 'operator', organization_admin: false })
    if (url.endsWith('/execution-grants'))
      return json([{ capability: 'operator', object_id: 'operator' }])
    if (url.endsWith('/run-setup/suggestions'))
      return options.method === 'POST'
        ? json({ id: 'suggestion-2', status: 'queued' }, 202)
        : history()
    if (url.endsWith('/runs/preflight'))
      return json({
        valid: true,
        checked_at: '2026-09-23T12:00:00Z',
        trigger: 'manual',
        window_starts_at: manifest.draft.window?.starts_at,
        window_ends_at: manifest.draft.window?.ends_at,
        max_operations: 1000,
        planned_attempts: 64,
        approval_required: false,
        environments: [],
        targets: [],
        recovery: [],
        issues: [],
        blockers: [],
      })
    if (url.endsWith('/runs') && options.method === 'POST') return json({ id: 'created-run' }, 201)
    if (url.endsWith('/runs')) return json([])
    throw new Error(`Unhandled fixture request ${url}`)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function bodies(fetch: ReturnType<typeof fixture>, suffix: string) {
  return fetch.mock.calls
    .filter(([url, options]) => String(url).endsWith(suffix) && options?.method === 'POST')
    .map(([, options]) => JSON.parse(String(options?.body)))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('describe what to check', () => {
  it('asks, reviews, uses suggestions, and records them only as provenance', async () => {
    const fetch = fixture(() => json([suggestion()]))
    mount()
    const prompt = await screen.findByLabelText('What should this run check?')
    expect(screen.getByRole('button', { name: 'Suggest checks' })).toBeDisabled()
    fireEvent.change(prompt, { target: { value: 'Watch occupancy until it is above 85%' } })
    expect(screen.getByLabelText('Run setup dirty')).toHaveTextContent('true')
    fireEvent.click(screen.getByRole('button', { name: 'Suggest checks' }))
    await waitFor(() => expect(prompt).toHaveValue(''))
    const [request] = bodies(fetch, '/run-setup/suggestions')
    expect(request).toEqual({
      preview_id: 'golden-preview',
      preview_digest: 'e'.repeat(64),
      prompt: 'Watch occupancy until it is above 85%',
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    })
    expect(await screen.findByText(/Watch occupancy, then judge both goals/)).toBeVisible()
    expect(screen.getByText('Which result shows the cots arrived?')).toBeVisible()
    expect(screen.getAllByText('Valid')).toHaveLength(4)
    expect(screen.getByText(/read “Read occupancy” until occupancy_percent/)).toBeVisible()
    expect(screen.queryByRole('group', { name: 'Check 1' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Use these suggestions' }))
    const watch = screen.getByRole('group', { name: 'Check 1' })
    expect(within(watch).getByLabelText('Reading')).toHaveValue('occupancy_percent')
    expect(screen.getByText(/Added to the forms below/)).toBeVisible()
    expect(screen.getByText(/These checks started from an assistant suggestion/)).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Check setup' }))
    await screen.findByText(/Ready to create/)
    const create = screen.getByRole('button', { name: 'Create pinned run' })
    await waitFor(() => expect(create).toBeEnabled())
    fireEvent.click(create)
    await screen.findByText(/Run created and pinned/)
    const [checked] = bodies(fetch, '/runs/preflight')
    const [created] = bodies(fetch, '/runs')
    expect(checked).toEqual(created)
    expect(created).toEqual({
      preview_id: 'golden-preview',
      preview_digest: 'e'.repeat(64),
      ...golden.run_create,
      suggestion_id: 'suggestion-1',
    })
  })

  it('stops recording a suggestion once every item it filled is removed', async () => {
    const fetch = fixture(() => json([suggestion()]))
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Use these suggestions' }))
    const linked = /These checks started from an assistant suggestion/
    expect(screen.getByText(linked)).toBeVisible()
    // Editing a suggested check keeps the link, even while its value is briefly empty.
    const value = screen.getByLabelText('Compare with · occupancy_percent')
    fireEvent.change(value, { target: { value: '' } })
    expect(screen.getByText(linked)).toBeVisible()
    fireEvent.change(value, { target: { value: '90' } })
    fireEvent.click(screen.getByRole('button', { name: 'Remove check 1' }))
    for (const name of ['Breach identified quickly', 'Ticket acknowledged'])
      fireEvent.click(
        within(screen.getByRole('group', { name })).getByLabelText(
          'Measure this goal from run evidence',
        ),
      )
    expect(screen.getByText(linked)).toBeVisible()
    fireEvent.click(
      within(screen.getByRole('group', { name: 'Open resource ticket' })).getByLabelText(
        'An operator handles it manually',
      ),
    )
    expect(screen.queryByText(linked)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Check setup' }))
    await screen.findByText(/Ready to create/)
    const create = screen.getByRole('button', { name: 'Create pinned run' })
    await waitFor(() => expect(create).toBeEnabled())
    fireEvent.click(create)
    await screen.findByText(/Run created and pinned/)
    const [checked] = bodies(fetch, '/runs/preflight')
    const [created] = bodies(fetch, '/runs')
    expect(created).toEqual(checked)
    expect(created).toEqual({
      preview_id: 'golden-preview',
      preview_digest: 'e'.repeat(64),
      trigger: 'manual',
      observations: [],
      objectives: [],
      recovery: [],
    })
  })

  it('does not link a suggestion again when adding it would change nothing', async () => {
    const fetch = fixture(() => json([suggestion()]))
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Use these suggestions' }))
    const linked = /These checks started from an assistant suggestion/
    fireEvent.click(screen.getByRole('button', { name: 'Don’t record the suggestion' }))
    expect(screen.queryByText(linked)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add to my checks' }))
    expect(screen.getByText('Nothing new to add. Your forms are unchanged.')).toBeVisible()
    expect(screen.queryByText(/Added to the forms below/)).toBeNull()
    expect(screen.queryByText(linked)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Check setup' }))
    await screen.findByText(/Ready to create/)
    const create = screen.getByRole('button', { name: 'Create pinned run' })
    await waitFor(() => expect(create).toBeEnabled())
    fireEvent.click(create)
    await screen.findByText(/Run created and pinned/)
    const [created] = bodies(fetch, '/runs')
    expect(created).toEqual({
      preview_id: 'golden-preview',
      preview_digest: 'e'.repeat(64),
      ...golden.run_create,
    })
  })

  it('keeps the linked suggestion when another one adds nothing new', async () => {
    const earlier = suggestion({ id: 'suggestion-0', prompt: 'Watch occupancy.' })
    const fetch = fixture(() => json([suggestion(), earlier]))
    mount()
    const [latest] = await screen.findAllByRole('button', { name: 'Use these suggestions' })
    fireEvent.click(latest)
    fireEvent.click(screen.getByText('Earlier requests (1)'))
    fireEvent.click(screen.getAllByRole('button', { name: 'Add to my checks' })[1])
    expect(screen.getByText('Nothing new to add. Your forms are unchanged.')).toBeVisible()
    expect(screen.getByText(/These checks started from an assistant suggestion/)).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Check setup' }))
    await screen.findByText(/Ready to create/)
    const create = screen.getByRole('button', { name: 'Create pinned run' })
    await waitFor(() => expect(create).toBeEnabled())
    fireEvent.click(create)
    await screen.findByText(/Run created and pinned/)
    expect(bodies(fetch, '/runs')[0].suggestion_id).toBe('suggestion-1')
  })

  it('asks before replacing checks the operator already set', async () => {
    fixture(() => json([suggestion({ observations: [], objectives: [], recovery: [] })]))
    mount()
    await screen.findByText(/judge both goals/)
    expect(screen.queryByRole('button', { name: 'Use these suggestions' })).toBeNull()
    cleanup()
    fixture(() => json([suggestion()]))
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a check' }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    fireEvent.click(await screen.findByRole('button', { name: 'Use these suggestions' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/Replace your current checks/))
    const watch = screen.getByRole('group', { name: 'Check 1' })
    expect(within(watch).getByLabelText('Reading')).toHaveValue('')
    expect(screen.queryByText(/started from an assistant suggestion/)).toBeNull()
    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Use these suggestions' }))
    expect(
      within(screen.getByRole('group', { name: 'Check 1' })).getByLabelText('Reading'),
    ).toHaveValue('occupancy_percent')
  })

  it('adds suggestions to existing checks without asking', async () => {
    fixture(() => json([suggestion()]))
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Add a check' }))
    const confirm = vi.spyOn(window, 'confirm')
    fireEvent.click(await screen.findByRole('button', { name: 'Add to my checks' }))
    expect(confirm).not.toHaveBeenCalled()
    expect(screen.getByRole('group', { name: 'Check 1' })).toBeVisible()
    expect(screen.queryByRole('group', { name: 'Check 2' })).toBeNull()
    expect(
      within(screen.getByRole('group', { name: 'Ticket acknowledged' })).getByLabelText(
        'Measure this goal from run evidence',
      ),
    ).toBeChecked()
  })

  it('shows invalid items but never uses them', async () => {
    const invalid = {
      item: { ...bindings.observations[0], field: 'invented' },
      valid: false,
      issues: ['Observation must compare a declared result'],
    }
    fixture(() => json([suggestion({ observations: [invalid], objectives: [], recovery: [] })]))
    mount()
    expect(await screen.findByText('Needs changes')).toBeVisible()
    expect(screen.getByText('Observation must compare a declared result')).toBeVisible()
    expect(screen.getByText(/None of these can be used as they are/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Use these suggestions' })).toBeDisabled()
  })

  it('keeps suggestions for an earlier preview out of the forms', async () => {
    const fetch = fixture(() => json([suggestion({ is_current: false })]))
    mount()
    expect(await screen.findByText('Earlier preview')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Use these suggestions' })).toBeNull()
    expect(screen.getByText(/reviewed against an earlier preview/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Ask again for the current preview' }))
    await waitFor(() => expect(bodies(fetch, '/run-setup/suggestions')).toHaveLength(1))
    expect(bodies(fetch, '/run-setup/suggestions')[0].prompt).toBe(suggestion().prompt)
  })

  it('offers a quick request for every goal and shows progress while it works', async () => {
    let calls = 0
    const fetch = fixture(() =>
      json(
        calls++ === 0
          ? []
          : [suggestion({ id: 'suggestion-2', status: 'running', summary: null, questions: [] })],
      ),
    )
    mount()
    await screen.findByText(/No suggestions yet/)
    fireEvent.click(screen.getByRole('button', { name: 'Suggest checks for every goal' }))
    expect(await screen.findByText('Suggesting checks…')).toBeVisible()
    expect(bodies(fetch, '/run-setup/suggestions')[0].prompt).toBe(EVERY_GOAL_PROMPT)
    expect(screen.getByRole('button', { name: 'Suggest checks for every goal' })).toBeDisabled()
    expect(screen.getByText(/working on the latest request/)).toBeVisible()
  })

  it('offers a failed request again without discarding what is typed', async () => {
    fixture(() =>
      json([
        suggestion({
          status: 'failed',
          error: 'The assistant’s answer did not match the run-check format.',
          summary: null,
          observations: [],
          objectives: [],
          recovery: [],
          questions: [],
        }),
      ]),
    )
    mount()
    const reuse = await screen.findByRole('button', { name: 'Use this request again' })
    expect(screen.getByText(/did not match the run-check format/)).toBeVisible()
    fireEvent.click(reuse)
    expect(screen.getByLabelText('What should this run check?')).toHaveValue(suggestion().prompt)
    expect(reuse).toBeDisabled()
  })

  it('explains when the service has no assistant and leaves the forms usable', async () => {
    fixture(() => json({ detail: 'The run-check assistant is not configured.' }, 503))
    mount()
    expect(await screen.findByText(/not configured for this deployment/)).toBeVisible()
    expect(screen.queryByLabelText('What should this run check?')).toBeNull()
    expect(screen.getByRole('button', { name: 'Add a check' })).toBeEnabled()
  })

  it('stays hidden when the capability is off', async () => {
    const fetch = fixture(() => json([]))
    mount(false)
    expect(await screen.findByRole('button', { name: 'Add a check' })).toBeEnabled()
    expect(screen.queryByRole('heading', { name: 'Describe what to check' })).toBeNull()
    expect(fetch.mock.calls.some(([url]) => String(url).includes('run-setup'))).toBe(false)
  })
})
