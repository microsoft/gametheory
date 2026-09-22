import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import type { components } from './api.generated'
import { ErrorNotice, useSession } from './api'
import { ConflictNotice, exportJson } from './PreparationShared'
import type { BoardSummary, BoardView } from './preparation'

export type BoardCreate = components['schemas']['BoardCreate']

export function Boards({ wid, editable }: { wid: string; editable: boolean }) {
  const { api } = useSession()
  const boards = useQuery({
    queryKey: [wid, 'boards'],
    queryFn: () => api.get<BoardSummary[]>(`/workspaces/${wid}/boards`),
  })
  return (
    <section className="stack" aria-labelledby="boards-heading">
      <div className="section-heading">
        <div>
          <h2 id="boards-heading">Game boards</h2>
          <p className="preparation-note">
            Prepare an exercise against a pinned published scenario. Previews and preparation
            approvals never authorize execution.
          </p>
        </div>
        {editable && (
          <Link className="text-action" to={`/w/${wid}?section=scenarios`}>
            Choose a published scenario <ArrowRight size={16} />
          </Link>
        )}
      </div>
      <ErrorNotice error={boards.error} />
      {boards.isPending && <p role="status">Loading game boards...</p>}
      {boards.error && (
        <button type="button" onClick={() => void boards.refetch()}>
          Retry game boards
        </button>
      )}
      {boards.data?.length === 0 && (
        <div className="empty glass">
          <h2>No preparation boards yet</h2>
          <p>
            {editable
              ? 'Open a scenario, publish a revision, then choose Create board under Review & history. The board pins that exact published snapshot.'
              : 'A workspace editor can create a board from a published scenario revision. You can then inspect its preparation and immutable history.'}
          </p>
        </div>
      )}
      <div className="card-grid">
        {boards.data?.map((board) => (
          <Link className="glass workspace-card" key={board.id} to={`/w/${wid}/boards/${board.id}`}>
            <h3>{board.name}</h3>
            <p>
              Published scenario revision {board.revision_version} · preparation version{' '}
              {board.version}
            </p>
            <p>
              {board.preparation_status === 'previewed'
                ? 'Static preview available'
                : 'Preparation draft'}
              {' · '}
              {board.approval_status === 'approved'
                ? 'Preparation approved, not execution authorized'
                : board.approval_status === 'invalid'
                  ? 'Preparation approval no longer valid'
                  : board.approval_status === 'rejected'
                    ? 'Preparation rejected'
                    : 'No preparation approval'}
            </p>
            <span className="pill">Execution disabled</span>
            <span className="text-action">
              Open board <ArrowRight size={16} />
            </span>
          </Link>
        ))}
      </div>
    </section>
  )
}

export function CreateBoardFromRevision({
  wid,
  scenarioId,
  revisionVersion,
  title,
  disabled,
  onInputChange,
  initialInput,
}: {
  wid: string
  scenarioId: string
  revisionVersion: number
  title: string
  disabled: boolean
  onInputChange: (revision: number, input: BoardCreate | null) => void
  initialInput?: BoardCreate
}) {
  const { api } = useSession()
  const cache = useQueryClient()
  const [open, setOpen] = useState(!!initialInput)
  const [name, setName] = useState(initialInput?.name ?? title)
  const path = `/workspaces/${wid}/boards`
  const create = useMutation({
    mutationFn: () => {
      const input: BoardCreate = {
        name: name.trim(),
        scenario_id: scenarioId,
        revision_version: revisionVersion,
      }
      return api.send<BoardView>(path, 'POST', input)
    },
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: [wid, 'boards'] })
    },
  })
  const reload = useMutation({
    mutationFn: () => api.get<BoardSummary[]>(path),
    onSuccess: (value) => {
      cache.setQueryData([wid, 'boards'], value)
      setName(title)
      create.reset()
    },
  })
  useEffect(() => {
    onInputChange(
      revisionVersion,
      open && !create.data
        ? { name, scenario_id: scenarioId, revision_version: revisionVersion }
        : null,
    )
  }, [open, name, scenarioId, revisionVersion, create.data, onInputChange])
  const exportInput = () =>
    exportJson(
      { name, scenario_id: scenarioId, revision_version: revisionVersion },
      'board-creation-input.json',
    )
  if (!open)
    return (
      <button type="button" disabled={disabled} onClick={() => setOpen(true)}>
        Create board
      </button>
    )
  if (create.data)
    return (
      <div className="notice" role="status">
        <strong>Board created from published revision {revisionVersion}.</strong>
        <p>
          <Link to={`/w/${wid}/boards/${create.data.id}`}>Open {create.data.name}</Link>
        </p>
        <p>
          Subsequent scenario edits and asset uploads will not alter its pinned source. Execution is
          disabled.
        </p>
      </div>
    )
  return (
    <section className="preparation-section">
      <h3>Create a preparation board</h3>
      <p className="preparation-note">
        Pin published revision {revisionVersion} of “{title}”, not the current mutable draft.
      </p>
      <ErrorNotice error={create.error ?? reload.error} />
      <ConflictNotice
        error={create.error}
        onExport={exportInput}
        onReload={() => reload.mutate()}
        pending={reload.isPending}
      />
      <form
        className="preparation-fields"
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate()
        }}
      >
        <label>
          New board name for revision {revisionVersion}
          <input
            value={name}
            required
            maxLength={160}
            disabled={disabled || create.isPending}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <div className="preparation-actions">
          <button className="primary" disabled={disabled || create.isPending || !name.trim()}>
            {create.isPending ? 'Creating...' : `Create board from revision ${revisionVersion}`}
          </button>
          <button
            type="button"
            disabled={create.isPending}
            onClick={() => {
              if (window.confirm('Discard this unsaved board name and close the creation form?'))
                setOpen(false)
            }}
          >
            Cancel board creation
          </button>
        </div>
      </form>
    </section>
  )
}
