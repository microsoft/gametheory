import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { useState, type ReactNode } from 'react'
import golden from '../../../backend/tests/fixtures/guided-run-setup.json'
import type { components } from './api.generated'
import { ApiError, createApi, SessionContext } from './api'
import { AdminSettings, PolicyEditor } from './AdminSettings'
import {
  BoardRuns,
  emptyRunSetup,
  ExerciseRunContent,
  runOptions,
  serviceMessage,
  type RunSetupInput,
} from './ExerciseRuns'
import type { Preview } from './preparation'
import type { Config } from './types'

const config: Config = {
  cloud: 'commercial',
  auth: {
    configured: true,
    client_id: 'fixture',
    authority: 'https://fixture.invalid',
    scope: 'fixture',
  },
  capabilities: {
    authoring: true,
    assets: false,
    planning: false,
    execution: true,
    run_assistant: false,
  },
  max_upload_bytes: 1024,
}
const policy: components['schemas']['EnvironmentPolicyView'] = {
  environment_id: '00000000-0000-4000-8000-000000000001',
  name: 'Production',
  classification: 'production',
  execution_enabled: false,
  approval_required: true,
  version: 3,
  updated_by: '00000000-0000-4000-8000-000000000002',
  updated_at: '2026-09-22T12:00:00Z',
}
function mount(element: ReactNode) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter([{ path: '*', element }])
  render(
    <QueryClientProvider client={cache}>
      <SessionContext.Provider value={{ api: createApi(async () => 'test-only'), config }}>
        <RouterProvider router={router} />
      </SessionContext.Provider>
    </QueryClientProvider>,
  )
  return cache
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('administrator environment policies', () => {
  it('locks production approval on and prevents classification downgrade', () => {
    mount(<PolicyEditor policy={policy} />)
    expect(screen.getByLabelText('Require independent execution approval')).toBeChecked()
    expect(screen.getByLabelText('Require independent execution approval')).toBeDisabled()
    expect(screen.getByLabelText('Environment classification')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save environment policy' })).toBeDisabled()
  })

  it('lets admins opt nonproduction into approval using a resource precondition', async () => {
    const nonproduction = {
      ...policy,
      classification: 'nonproduction' as const,
      name: 'QA',
      approval_required: false,
    }
    const fetch = vi.fn(
      async (_url: string, _options: RequestInit) =>
        new Response(JSON.stringify({ ...nonproduction, approval_required: true, version: 4 }), {
          status: 200,
        }),
    )
    vi.stubGlobal('fetch', fetch)
    mount(<PolicyEditor policy={nonproduction} />)
    fireEvent.click(screen.getByLabelText('Require independent execution approval'))
    fireEvent.click(screen.getByRole('button', { name: 'Save environment policy' }))
    await screen.findByText(/Policy saved/)
    const request = fetch.mock.calls[0]
    expect(request[0]).toBe(`/api/admin/environment-policies/${policy.environment_id}`)
    const options = request[1]
    expect(new Headers(options.headers).get('If-Match')).toBe('"3"')
    expect(JSON.parse(String(options.body))).toEqual({
      classification: 'nonproduction',
      execution_enabled: false,
      approval_required: true,
    })
  })

  it('preserves local policy input after a concurrent save conflict', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail":"Environment policy changed."}', { status: 409 })),
    )
    mount(
      <PolicyEditor
        policy={{
          ...policy,
          name: 'QA',
          classification: 'nonproduction',
          approval_required: false,
        }}
      />,
    )
    fireEvent.click(screen.getByLabelText('Require independent execution approval'))
    fireEvent.click(screen.getByRole('button', { name: 'Save environment policy' }))
    await screen.findByText(/The saved version changed/)
    expect(screen.getByLabelText('Require independent execution approval')).toBeChecked()
    expect(screen.getByRole('button', { name: 'Export my input' })).toBeVisible()
  })

  it('does not fetch administrator policies for a nonadmin', async () => {
    const fetch = vi.fn(
      async (_url: string) => new Response('{"object_id":"viewer","organization_admin":false}'),
    )
    vi.stubGlobal('fetch', fetch)
    mount(<AdminSettings />)
    await screen.findByText(/Organization administrator access is required/)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/me')
  })
})

describe('restricted run input', () => {
  it('accepts only declarative binding arrays', () => {
    expect(runOptions('{"observations":[],"objectives":[],"recovery":[]}')).toEqual({
      observations: [],
      objectives: [],
      recovery: [],
    })
  })
  it.each([
    '{"execution_authorized":true}',
    '{"endpoint":"https://different.invalid"}',
    '{"observations":null}',
    '{"objectives":[],"objectives":[]}',
    '[]',
    '{"recovery":[NaN]}',
  ])('rejects overrides and invalid JSON: %s', (text) => {
    expect(() => runOptions(text)).toThrow()
  })
})

function runFixture() {
  return {
    id: 'fixture-run',
    board_id: 'fixture-board',
    version: 2,
    state: 'prepared',
    phase: 'exercise',
    operator: 'fixture-operator',
    created_at: '2026-09-22T12:00:00Z',
    manifest_digest: 'a'.repeat(64),
    context_id: 'fixture-context',
    approval_required: false,
    approval_status: 'not_required',
    blockers: ['Target readiness has not been recorded'],
    can_operate: true,
    can_stop: true,
    can_review: false,
    manifest: {
      trigger: 'manual',
      recovery: [],
      observations: [],
      objectives: [],
      preparation: {
        draft: { name: 'Fixture only', steps: [] },
        scenario: { content: { objectives: [] } },
        configurations: [],
      },
    },
    steps: [],
    events: [],
    findings: [],
  }
}

describe('run controls', () => {
  it('shows approval-exempt policy honestly and keeps blocked starts disabled', async () => {
    const run = runFixture()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (url: string) =>
          new Response(
            JSON.stringify(
              url.endsWith('/me')
                ? { object_id: 'fixture-operator', organization_admin: false }
                : run,
            ),
            { headers: { ETag: '"2"' } },
          ),
      ),
    )
    mount(<ExerciseRunContent wid="workspace" rid="fixture-run" />)
    await screen.findByText('Approval not required by environment policy')
    fireEvent.change(screen.getByLabelText('Operator / reviewer note'), {
      target: { value: 'Start only when ready' },
    })
    expect(screen.getByRole('button', { name: 'Start run' })).toBeDisabled()
    expect(
      screen.queryByRole('button', { name: 'Record execution decision' }),
    ).not.toBeInTheDocument()
    expect(screen.getByText('Target readiness has not been recorded')).toBeVisible()
  })

  it('does not turn an unknown outcome into success or offer a new start', async () => {
    const run = {
      ...runFixture(),
      state: 'stopped_incomplete',
      steps: [
        {
          step_id: 'step',
          phase: 'exercise',
          state: 'unknown',
          result: {},
          reason: 'Provider outcome is unknown',
          samples: 1,
          started_at: '2026-09-22T12:00:00Z',
          finished_at: null,
        },
      ],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (url: string) =>
          new Response(
            JSON.stringify(
              url.endsWith('/me')
                ? { object_id: 'fixture-operator', organization_admin: false }
                : run,
            ),
            { headers: { ETag: '"2"' } },
          ),
      ),
    )
    mount(<ExerciseRunContent wid="workspace" rid="fixture-run" />)
    await screen.findByText('Provider outcome is unknown')
    expect(screen.getByText('stopped incomplete')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Start run' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'External outcome report' })).toBeVisible()
  })

  it('preserves an operator note after an action conflict', async () => {
    const run = runFixture()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, options: RequestInit) => {
        if (options.method === 'POST')
          return new Response('{"detail":"Run changed."}', { status: 409 })
        return new Response(
          JSON.stringify(
            url.endsWith('/me')
              ? { object_id: 'fixture-operator', organization_admin: false }
              : run,
          ),
          { headers: { ETag: '"2"' } },
        )
      }),
    )
    mount(<ExerciseRunContent wid="workspace" rid="fixture-run" />)
    await screen.findByText('Approval not required by environment policy')
    fireEvent.change(screen.getByLabelText('Operator / reviewer note'), {
      target: { value: 'Retain this note' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Authorize under current policy' }))
    await waitFor(() => expect(screen.getByText('Run changed.')).toBeVisible())
    expect(screen.getByLabelText('Operator / reviewer note')).toHaveValue('Retain this note')
  })
})

function goldenPreview() {
  return {
    id: 'golden-preview',
    board_id: golden.preview_manifest.board_id,
    board_version: 3,
    sequence: 1,
    digest: 'e'.repeat(64),
    manifest: golden.preview_manifest,
    findings: [],
    created_by: 'author',
    created_at: '2026-09-22T12:00:00Z',
    is_current: true,
    execution_authorized: false,
    execution_eligible: false,
  } as unknown as Preview
}

function preflightFixture(blockers: unknown[] = []) {
  return {
    valid: true,
    checked_at: '2026-09-23T12:00:00Z',
    trigger: 'manual',
    window_starts_at: '2026-09-23T12:00:00Z',
    window_ends_at: '2026-09-23T14:00:00Z',
    max_operations: 1000,
    planned_attempts: 64,
    approval_required: true,
    environments: [],
    targets: [],
    recovery: [],
    issues: [],
    blockers,
  }
}

function BoardRunsHarness({ onCreate }: { onCreate?: () => void }) {
  const [input, setInput] = useState<RunSetupInput>(emptyRunSetup)
  return (
    <>
      <BoardRuns
        wid="workspace"
        bid="board"
        version={3}
        preview={goldenPreview()}
        unsaved={false}
        input={input}
        onInputChange={setInput}
        onOpenPreparation={() => onCreate?.()}
      />
      <output aria-label="Run setup dirty">{String(input.dirty)}</output>
    </>
  )
}

function boardFetch(responses: { preflight?: () => Response; create?: () => Response } = {}) {
  return vi.fn(async (url: string, options: RequestInit = {}) => {
    if (url.endsWith('/me'))
      return new Response(JSON.stringify({ object_id: 'operator', organization_admin: false }))
    if (url.endsWith('/execution-grants'))
      return new Response(JSON.stringify([{ capability: 'operator', object_id: 'operator' }]))
    if (url.endsWith('/runs/preflight'))
      return responses.preflight?.() ?? new Response(JSON.stringify(preflightFixture()))
    if (url.endsWith('/runs') && options.method === 'POST')
      return responses.create?.() ?? new Response(JSON.stringify({ id: 'created-run' }))
    if (url.endsWith('/runs')) return new Response('[]')
    throw new Error(`Unhandled fixture request ${url}`)
  })
}

describe('guided run setup', () => {
  it('requires a current check before creating and marks it stale after an edit', async () => {
    const fetch = boardFetch({
      preflight: () =>
        new Response(
          JSON.stringify(
            preflightFixture([
              {
                code: 'environment_execution_disabled',
                message: 'Resource ticket API: Execution is not enabled for Training lab',
                remedy: 'environment_policy',
              },
            ]),
          ),
        ),
    })
    vi.stubGlobal('fetch', fetch)
    mount(<BoardRunsHarness />)
    const create = await screen.findByRole('button', { name: 'Create pinned run' })
    expect(create).toBeDisabled()
    expect(await screen.findByText('Check the setup first.')).toBeVisible()
    fireEvent.click(await screen.findByRole('button', { name: 'Check setup' }))
    await screen.findByText(/Ask an organization administrator to update Settings → Environments/)
    expect(screen.getByText(/Independent execution approval is required/)).toBeVisible()
    await waitFor(() => expect(create).toBeEnabled())
    const request = fetch.mock.calls.find(([url]) => String(url).endsWith('/preflight'))!
    expect(new Headers(request[1]?.headers).get('If-Match')).toBe('"3"')
    expect(JSON.parse(String(request[1]?.body))).toEqual({
      preview_id: 'golden-preview',
      preview_digest: 'e'.repeat(64),
      trigger: 'manual',
      observations: [],
      objectives: [],
      recovery: [],
    })
    fireEvent.click(screen.getByLabelText(/Automatically at the window start/))
    expect(screen.getByLabelText('Run setup dirty')).toHaveTextContent('true')
    expect(create).toBeDisabled()
    expect(
      screen.getByText(/The setup changed after the last check/, { selector: 'span' }),
    ).toBeVisible()
  })

  it('keeps the run setup when creation conflicts', async () => {
    vi.stubGlobal(
      'fetch',
      boardFetch({
        create: () =>
          new Response('{"detail":"Freeze and select the exact current preparation preview"}', {
            status: 409,
          }),
      }),
    )
    mount(<BoardRunsHarness />)
    fireEvent.click(await screen.findByRole('button', { name: 'Add a check' }))
    const reading = within(screen.getByRole('group', { name: 'Check 1' })).getByLabelText('Reading')
    fireEvent.change(reading, { target: { value: 'occupancy_percent' } })
    fireEvent.change(screen.getByLabelText('Compare with · occupancy_percent'), {
      target: { value: '85' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Check setup' }))
    await screen.findByText(/Ready to create/)
    fireEvent.click(screen.getByRole('button', { name: 'Create pinned run' }))
    await screen.findByText('Freeze and select the exact current preparation preview')
    expect(screen.getByText(/Your run setup is kept/)).toBeVisible()
    expect(reading).toHaveValue('occupancy_percent')
    expect(screen.getByLabelText('Run setup dirty')).toHaveTextContent('true')
  })

  it('explains service validation errors in plain field paths', () => {
    const error = serviceMessage(
      new ApiError(422, '[]', [
        {
          loc: ['body', 'observations', 0, 'interval_seconds'],
          msg: 'Input should be at most 3600',
        },
      ]),
    )
    expect((error as Error).message).toBe(
      'observations → 0 → interval_seconds: Input should be at most 3600',
    )
  })
})

describe('run launch checklist', () => {
  it('shows each blocker with who can resolve it', async () => {
    const run = {
      ...runFixture(),
      blockers: [
        'Exercise runtime is disabled',
        'Authorize this exact run under the current environment policies',
      ],
      blocker_details: [
        { code: 'runtime_disabled', message: 'Exercise runtime is disabled', remedy: 'runtime' },
        {
          code: 'authorization_required',
          message: 'Authorize this exact run under the current environment policies',
          remedy: 'authorize',
        },
      ],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (url: string) =>
          new Response(
            JSON.stringify(
              url.endsWith('/me')
                ? { object_id: 'fixture-operator', organization_admin: true }
                : run,
            ),
            { headers: { ETag: '"2"' } },
          ),
      ),
    )
    mount(<ExerciseRunContent wid="workspace" rid="fixture-run" />)
    await screen.findByRole('heading', { name: 'Launch checklist' })
    expect(screen.getByText(/deployment operator to enable it/)).toBeVisible()
    expect(screen.getByText(/in Operator controls/)).toBeVisible()
    expect(screen.getByText('Enter an operator note to enable these actions.')).toBeVisible()
    expect(screen.getByText(/Up to 0 of 1,000/)).toBeVisible()
  })
})
