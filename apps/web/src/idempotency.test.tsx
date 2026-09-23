import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import golden from '../../../backend/tests/fixtures/guided-run-setup.json'
import { BoardEditor } from './BoardEditor'
import { PreviewDetails } from './BoardHistory'
import type { BoardDraft, BoardView, Configuration, Preview } from './preparation'

const manifest = golden.preview_manifest as unknown as Preview['manifest']
const configurations = manifest.configurations.map((item) => ({
  ...item,
  withdrawn_at: null,
  withdrawn_by: null,
})) as Configuration[]
const raise = manifest.draft.steps![0]

afterEach(cleanup)

describe('dispatcher-owned SQL idempotency keys', () => {
  it('explains the key is automatic and clears a typed value', () => {
    const draft = structuredClone(manifest.draft) as BoardDraft
    draft.steps![0].parameters = { ...raise.parameters, idempotency_key: 'typed-by-hand' }
    const onChange = vi.fn()
    render(
      <BoardEditor
        draft={draft}
        scenario={manifest.scenario as BoardView['scenario']}
        configurations={configurations}
        editable
        onChange={onChange}
      />,
    )
    expect(screen.getAllByText(/Supplied automatically during exercise runs/)).toHaveLength(1)
    expect(screen.queryByLabelText('idempotency_key')).not.toBeInTheDocument()
    expect(screen.getByText(/A typed value here would block run creation/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Clear idempotency_key' }))
    const next = onChange.mock.calls[0][0] as BoardDraft
    expect(next.steps![0].parameters).toEqual(raise.parameters)
  })

  it('labels the unresolved key finding in a SQL preview as expected', () => {
    const preview: Preview = {
      id: 'preview',
      board_id: manifest.board_id,
      board_version: manifest.board_version,
      sequence: 1,
      digest: 'e'.repeat(64),
      manifest,
      findings: [
        {
          code: 'missing_parameter',
          severity: 'blocker',
          message: 'Required parameter idempotency_key is unresolved.',
          path: `/draft/steps/${raise.id}/parameters/idempotency_key`,
          step_id: raise.id,
        },
        {
          code: 'missing_parameter',
          severity: 'blocker',
          message: 'Required parameter title is unresolved.',
          path: `/draft/steps/${manifest.draft.steps![2].id}/parameters/title`,
          step_id: manifest.draft.steps![2].id,
        },
      ],
      created_by: 'author',
      created_at: '2026-09-22T12:00:00Z',
      is_current: true,
      execution_authorized: false,
      execution_eligible: false,
    }
    render(<PreviewDetails preview={preview} />)
    expect(screen.getAllByText(/Expected for SQL steps/)).toHaveLength(1)
  })
})
