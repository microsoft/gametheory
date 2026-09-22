import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ApiError, createApi } from './api'
import { ConflictNotice, ExecutionBoundary } from './PreparationShared'
import { parseUniqueJson } from './strictJson'

afterEach(() => vi.unstubAllGlobals())

describe('conditional preparation requests', () => {
  it('reads a strong board precondition and uses it for decision revocation', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('{"version":4}', { headers: { ETag: '"4"' } }))
      .mockResolvedValueOnce(new Response('{"execution_authorized":false}'))
    vi.stubGlobal('fetch', fetch)
    const api = createApi(async () => 'fixture-token')
    const result = await api.read<{ version: number }>('/board')
    expect(result).toEqual({ data: { version: 4 }, version: 4 })
    await api.send('/board/approvals/decision/revoke', 'POST', undefined, result.version)
    expect((fetch.mock.calls[1][1].headers as Headers).get('if-match')).toBe('"4"')
  })

  it.each([undefined, 'W/"4"', '4', '"4", "5"', '"9007199254740992"'])(
    'refuses an absent or unsafe ETag (%s)',
    async (etag) => {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue(new Response('[]', { headers: etag ? { ETag: etag } : {} })),
      )
      await expect(createApi(async () => 'fixture').read('/board')).rejects.toThrow(/version/i)
    },
  )
  it('reads collections without ETags and does not invent append-only or grant preconditions', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('[]'))
      .mockResolvedValueOnce(new Response('{}'))
      .mockResolvedValueOnce(new Response('{}'))
      .mockResolvedValueOnce(new Response('{}'))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)
    const api = createApi(async () => 'fixture')
    expect(await api.get<unknown[]>('/configurations')).toEqual([])
    await api.send('/configurations', 'POST', {})
    await api.send('/boards', 'POST', {})
    await api.send('/approvers', 'PUT', {})
    await api.remove('/approvers/object')
    for (const call of fetch.mock.calls)
      expect((call[1].headers as Headers).has('If-Match')).toBe(false)
  })
})

describe('catalog JSON intake', () => {
  it('preserves scalar types and null without prototype properties', () => {
    expect(parseUniqueJson('{"fields":[true,12,0.4,null,"12"],"__proto__":"literal"}')).toEqual({
      fields: [true, 12, 0.4, null, '12'],
      ['__proto__']: 'literal',
    })
  })
  it.each([
    '{"name":"first","name":"second"}',
    '{"name":"first","\\u006eame":"second"}',
    '{"operations":[{"key":"first","key":"second"}]}',
  ])('rejects duplicate keys before JSON serialization can hide them', (input) => {
    expect(() => parseUniqueJson(input)).toThrow(/Duplicate JSON property/)
  })
  it.each(['1e999', '{"value":NaN}', '[1,]', '{"a":1,}', '{"a":01}', 'true false'])(
    'rejects invalid JSON or nonfinite values (%s)',
    (input) => {
      expect(() => parseUniqueJson(input)).toThrow()
    },
  )
})

describe('preparation boundaries', () => {
  it('never offers execution or implies approval authorizes a run', () => {
    render(<ExecutionBoundary />)
    expect(screen.getByRole('button', { name: 'Execution disabled' })).toBeDisabled()
    expect(screen.getByText(/Preparation approval is not authorization/)).toBeVisible()
  })
  it('offers export and confirmed reload after a conflict without automatic discard', () => {
    const reload = vi.fn()
    render(
      <ConflictNotice
        error={new ApiError(409, 'Changed', null)}
        onExport={vi.fn()}
        onReload={reload}
      />,
    )
    expect(screen.getByRole('button', { name: 'Export my input' })).toBeVisible()
    expect(reload).not.toHaveBeenCalled()
  })
})
