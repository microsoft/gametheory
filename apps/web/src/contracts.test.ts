import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createApi } from './api'
import { makeMermaid } from './FlowEditor'
import { normalizeScenario } from './types'

afterEach(() => vi.unstubAllGlobals())

describe('scenario projections', () => {
  it('normalizes optional API defaults without mutating the response', () => {
    const raw = {
      id: 's',
      workspace_id: 'w',
      version: 1,
      updated_at: '',
      content: { schema_version: 1 as const, title: 'Scenario' },
    }
    expect(normalizeScenario(raw).content.nodes).toEqual([])
    expect(raw.content).toEqual({ schema_version: 1, title: 'Scenario' })
  })
  it('escapes diagram control characters instead of executing injected syntax', () => {
    const scenario = normalizeScenario({
      id: 's',
      workspace_id: 'w',
      version: 1,
      updated_at: '',
      content: {
        schema_version: 1,
        title: 'Exercise',
        nodes: [
          { id: '1234', label: '85% "]', kind: 'condition', detail: '', position: { x: 0, y: 0 } },
        ],
      },
    })
    expect(makeMermaid(scenario.content)).toContain('85#37; #34;#93;')
    expect(makeMermaid(scenario.content)).not.toContain('85% "]')
  })
})

describe('authenticated API', () => {
  it('sends exact revision preconditions and bearer tokens', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"version":2}'))
    vi.stubGlobal('fetch', fetch)
    await createApi(async () => 'test-token').send('/scenario', 'PUT', { title: 'Edited' }, 1)
    const headers = fetch.mock.calls[0][1].headers as Headers
    expect(headers.get('if-match')).toBe('"1"')
    expect(headers.get('authorization')).toBe('Bearer test-token')
  })
  it('preserves conflict information instead of treating it as success', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ detail: { message: 'Draft changed', current: { version: 3 } } }),
            { status: 409 },
          ),
        ),
    )
    await expect(
      createApi(async () => 'test').send('/scenario', 'PUT', {}, 1),
    ).rejects.toMatchObject({
      status: 409,
      message: 'Draft changed',
      detail: { current: { version: 3 } },
    } satisfies Partial<ApiError>)
  })
})
