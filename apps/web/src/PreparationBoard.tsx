import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Save } from 'lucide-react'
import { ApiError, ErrorNotice, useSession } from './api'
import { BoardEditor } from './BoardEditor'
import { ApprovalHistory, approvalLabel, BoardProvenance, PreviewDetails } from './BoardHistory'
import { ConflictNotice, ExecutionBoundary, exportJson, UnsavedChanges } from './PreparationShared'
import {
  draftErrors,
  preparationReason,
  timezoneDateTime,
  type Approval,
  type ApprovalInput,
  type BoardDraft,
  type BoardView,
  type Configuration,
  type Me,
  type Preview,
} from './preparation'
import type { Connection } from './types'
import { BoardRuns } from './ExerciseRuns'

export function PreparationBoard() {
  const { wid = '', bid = '' } = useParams()
  return <PreparationBoardContent key={`${wid}/${bid}`} wid={wid} bid={bid} />
}

export function PreparationBoardContent({ wid, bid }: { wid: string; bid: string }) {
  const { api, config: appConfig } = useSession()
  const cache = useQueryClient()
  const path = `/workspaces/${wid}/boards/${bid}`
  const key = [wid, 'board', bid]
  const [base, setBase] = useState<BoardView>()
  const [draft, setDraft] = useState<BoardDraft>()
  const [pane, setPane] = useState<'draft' | 'preview' | 'history' | 'runs'>('draft')
  const [selectedPreviewId, setSelectedPreviewId] = useState('')
  const [decision, setDecision] = useState<Approval['decision'] | ''>('')
  const [expires, setExpires] = useState('')
  const [note, setNote] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [notice, setNotice] = useState('')
  const [now, setNow] = useState(Date.now)
  async function readBoard() {
    const response = await api.read<BoardView>(path)
    if (response.data.version !== response.version)
      throw new Error(
        'The service returned conflicting board versions. Reload before making changes.',
      )
    return response.data
  }
  const board = useQuery({
    queryKey: key,
    queryFn: readBoard,
    refetchInterval: 30000,
  })
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get<Me>('/me') })
  const previews = useQuery({
    queryKey: [...key, 'previews'],
    queryFn: () => api.get<Preview[]>(`${path}/previews`),
    refetchInterval: 30000,
  })
  const approvals = useQuery({
    queryKey: [...key, 'approvals'],
    queryFn: () => api.get<Approval[]>(`${path}/approvals`),
    refetchInterval: 30000,
  })
  const previewReferenceUnavailable = !!board.data?.approval_blockers.includes(
    'preview_reference_unavailable',
  )
  const boardAccessDenied =
    board.error instanceof ApiError && [403, 404].includes(board.error.status)
  const inventory = useQuery({
    queryKey: [wid, 'connections'],
    queryFn: () => api.get<Connection[]>(`/workspaces/${wid}/connections`),
  })
  const configurationQueries = useQueries({
    queries: (inventory.data ?? [])
      .filter((connection) => connection.kind !== 'mcp')
      .map((connection) => ({
        queryKey: [wid, 'configurations', connection.id],
        queryFn: () =>
          api.get<Configuration[]>(
            `/workspaces/${wid}/connections/${connection.id}/configurations`,
          ),
      })),
  })
  const configurations = configurationQueries.flatMap((query) =>
    query.error ||
    boardAccessDenied ||
    (previewReferenceUnavailable && query.dataUpdatedAt < board.dataUpdatedAt)
      ? []
      : (query.data ?? []),
  )
  const configurationError =
    inventory.error ?? configurationQueries.find((query) => query.error)?.error
  const configurationsPending =
    inventory.isPending || configurationQueries.some((query) => query.isPending)
  useEffect(() => {
    if (board.data && !base) {
      setBase(board.data)
      setDraft(board.data.draft)
      setSelectedPreviewId(board.data.latest_preview?.id ?? '')
    }
  }, [board.data, base])
  useEffect(() => {
    if (!previewReferenceUnavailable) return
    void cache.invalidateQueries({ queryKey: [wid, 'connections'] })
    void cache.invalidateQueries({ queryKey: [wid, 'configurations'] })
    void cache.invalidateQueries({ queryKey: [wid, 'board', bid, 'previews'] })
  }, [previewReferenceUnavailable, board.dataUpdatedAt, cache, wid, bid])
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000)
    return () => window.clearInterval(timer)
  }, [])
  const dirty = !!draft && !!base && JSON.stringify(draft) !== JSON.stringify(base.draft)
  const reviewDirty = !!decision || !!expires || !!note || acknowledged
  const inputErrors = useMemo(
    () => (draft ? draftErrors(draft, configurations) : []),
    [draft, configurations],
  )
  const displayed = board.data ?? base
  const behind = !!base && !!board.data && base.version !== board.data.version
  const availablePreviews =
    previews.error || previewReferenceUnavailable || boardAccessDenied ? undefined : previews.data
  const selectedPreview =
    availablePreviews?.find((preview) => preview.id === selectedPreviewId) ??
    (!board.error &&
    !previewReferenceUnavailable &&
    displayed?.latest_preview?.id === selectedPreviewId
      ? displayed.latest_preview
      : undefined)
  function accepted(value: BoardView, submitted?: BoardDraft) {
    setBase(value)
    setDraft((latest) =>
      submitted && JSON.stringify(latest) !== JSON.stringify(submitted) ? latest : value.draft,
    )
    cache.setQueryData(key, value)
    void cache.invalidateQueries({ queryKey: [wid, 'boards'] })
  }
  async function refreshHistory() {
    await Promise.all([
      cache.invalidateQueries({ queryKey: key }),
      cache.invalidateQueries({ queryKey: [wid, 'boards'] }),
    ])
  }
  const save = useMutation({
    mutationFn: ({ content, version }: { content: BoardDraft; version: number }) =>
      api.send<BoardView>(path, 'PUT', content, version),
    onSuccess: async (value, submitted) => {
      accepted(value, submitted.content)
      setNotice(
        'Preparation draft saved. Any material change requires a fresh preview and approval.',
      )
      await refreshHistory()
    },
  })
  const freeze = useMutation({
    mutationFn: () => {
      if (!base || dirty)
        throw new Error('Save the exact preparation draft before freezing a preview.')
      return api.send<Preview>(`${path}/previews`, 'POST', undefined, base.version)
    },
    onSuccess: async (value) => {
      setSelectedPreviewId(value.id)
      setAcknowledged(false)
      setPane('preview')
      setNotice(
        `Preview ${value.sequence} froze saved board version ${value.board_version}. Live prerequisites remain unverified.`,
      )
      await refreshHistory()
    },
  })
  const recordDecision = useMutation({
    mutationFn: () => {
      if (!base || !selectedPreview || !decision || !acknowledged || !note.trim())
        throw new Error(
          'Select a preview, decision, explicit expiry, acknowledgement, and reviewer note.',
        )
      const expiry = Date.parse(expires)
      const currentTime = Date.now()
      if (!Number.isFinite(expiry) || expiry <= currentTime || expiry > currentTime + 30 * 86400000)
        throw new Error('Choose an explicit future expiry within thirty days.')
      const input: ApprovalInput = {
        preview_id: selectedPreview.id,
        digest: selectedPreview.digest,
        decision,
        expires_at: timezoneDateTime(expires),
        acknowledge_unverified: true,
        note: note.trim(),
      }
      return api.send<Approval>(`${path}/approvals`, 'POST', input, base.version)
    },
    onSuccess: async (value) => {
      setDecision('')
      setExpires('')
      setNote('')
      setAcknowledged(false)
      setNotice(
        `Preparation ${value.decision} recorded. This decision does not authorize execution.`,
      )
      await refreshHistory()
    },
  })
  const revoke = useMutation({
    mutationFn: (approval: Approval) => {
      if (!base) throw new Error('Load the current board before revoking a decision.')
      return api.send<Approval>(
        `${path}/approvals/${approval.id}/revoke`,
        'POST',
        undefined,
        base.version,
      )
    },
    onSuccess: async () => {
      setNotice('Preparation decision revoked. Its immutable history is retained.')
      await refreshHistory()
    },
  })
  const reload = useMutation({
    mutationFn: readBoard,
    onSuccess: async (value) => {
      accepted(value)
      setSelectedPreviewId(value.latest_preview?.id ?? '')
      setDecision('')
      setExpires('')
      setNote('')
      setAcknowledged(false)
      save.reset()
      freeze.reset()
      recordDecision.reset()
      revoke.reset()
      setNotice(
        'Loaded the latest saved preparation. Review the current snapshot before making a decision.',
      )
      await refreshHistory()
    },
  })
  const busy =
    save.isPending ||
    freeze.isPending ||
    recordDecision.isPending ||
    revoke.isPending ||
    reload.isPending
  const error =
    board.error ??
    me.error ??
    save.error ??
    freeze.error ??
    recordDecision.error ??
    revoke.error ??
    reload.error
  const conflict = behind ? new ApiError(409, 'A newer preparation version exists.', null) : error
  const exportInput = () =>
    exportJson(
      {
        draft,
        review_input: reviewDirty
          ? {
              preview_id: selectedPreviewId,
              decision,
              expires_at: expires,
              note,
              acknowledge_unverified: acknowledged,
            }
          : undefined,
      },
      `board-${bid}-local-input.json`,
    )
  if (!base || !draft || !displayed)
    return (
      <main className="page">
        <Link to={`/w/${wid}?section=boards`}>Back to game boards</Link>
        <ErrorNotice error={error} />
        {!error && <p role="status">Loading preparation board...</p>}
      </main>
    )
  const canEdit = displayed.can_edit && !board.error
  const contributor =
    me.data &&
    (displayed.created_by === me.data.object_id ||
      displayed.contributors?.includes(me.data.object_id))
  const canApprove = displayed.can_approve && !!me.data && !contributor && !board.error && !me.error
  const previewCurrent =
    !!selectedPreview &&
    selectedPreview.is_current &&
    selectedPreview.board_version === base.version &&
    !behind
  const approvalDisabled = !canApprove || !previewCurrent || dirty || busy
  const expiryValid =
    Number.isFinite(Date.parse(expires)) &&
    Date.parse(expires) > now &&
    Date.parse(expires) <= now + 30 * 86400000
  return (
    <main className="page">
      <Link className="breadcrumb" to={`/w/${wid}?section=boards`}>
        <ArrowLeft size={14} /> Game boards
      </Link>
      <div className="page-heading">
        <div>
          <h1>{draft.name || base.name}</h1>
          <div className="toolbar">
            <span className="pill">Saved version {base.version}</span>
            <span className="preparation-status">
              {dirty ? 'Unsaved preparation changes' : 'Saved preparation'}
            </span>
          </div>
          <p>
            Bind proposed operations, freeze exact inputs, and request a separate preparation
            review.
          </p>
        </div>
        <div className="toolbar">
          <button type="button" onClick={exportInput}>
            Export local input
          </button>
          <button
            type="button"
            disabled={!canEdit || !dirty || busy || inputErrors.length > 0}
            onClick={() => save.mutate({ content: draft, version: base.version })}
          >
            <Save size={16} />
            {save.isPending ? 'Saving...' : 'Save preparation'}
          </button>
          <button
            type="button"
            className="primary"
            disabled={
              !canEdit ||
              dirty ||
              busy ||
              behind ||
              inputErrors.length > 0 ||
              configurationsPending ||
              !!configurationError
            }
            onClick={() => freeze.mutate()}
          >
            {freeze.isPending ? 'Freezing...' : 'Freeze saved preview'}
          </button>
        </div>
      </div>
      <ExecutionBoundary executionAvailable={appConfig.capabilities.execution} />
      <ErrorNotice error={error} />
      <ConflictNotice
        error={conflict}
        onExport={exportInput}
        onReload={() => reload.mutate()}
        pending={busy}
      />
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      {dirty && (
        <p className="preparation-note">
          Preview and approval use saved inputs only. Save this preparation before freezing a new
          preview.
        </p>
      )}
      {!board.error && displayed.current_approval && (
        <section className="notice" aria-label="Current preparation decision">
          <strong>{approvalLabel(displayed.current_approval, now)}</strong>
          <p>
            Decision on board version {displayed.current_approval.board_version}; expires{' '}
            {new Date(displayed.current_approval.expires_at).toLocaleString()}.
          </p>
          {dirty && (
            <p>
              Your unsaved changes are not covered by this decision. Save, freeze a new preview, and
              request a fresh preparation review.
            </p>
          )}
          {displayed.current_approval.validity.reasons?.length > 0 && (
            <ul>
              {displayed.current_approval.validity.reasons.map((reason) => (
                <li key={reason}>{preparationReason(reason)}</li>
              ))}
            </ul>
          )}
        </section>
      )}
      <nav className="tabs" aria-label="Board sections">
        <button type="button" aria-current={pane === 'draft'} onClick={() => setPane('draft')}>
          Preparation
        </button>
        <button type="button" aria-current={pane === 'preview'} onClick={() => setPane('preview')}>
          Preview & review
        </button>
        <button type="button" aria-current={pane === 'history'} onClick={() => setPane('history')}>
          History
        </button>
        <button type="button" aria-current={pane === 'runs'} onClick={() => setPane('runs')}>
          Exercise runs
        </button>
      </nav>
      <div className="preparation-layout">
        <div className="stack">
          {pane === 'runs' && (
            <BoardRuns
              wid={wid}
              bid={bid}
              version={base.version}
              preview={selectedPreview}
              unsaved={dirty || behind}
            />
          )}
          {pane === 'draft' && (
            <>
              <ErrorNotice error={configurationError} />
              {configurationError && (
                <p className="notice">
                  Registered configurations are unavailable. Existing input is preserved; do not
                  treat this as an empty catalog.{' '}
                  <button
                    type="button"
                    onClick={() => {
                      void inventory.refetch()
                      for (const query of configurationQueries) void query.refetch()
                    }}
                  >
                    Retry configuration loading
                  </button>
                </p>
              )}
              {configurationsPending && (
                <p role="status">Loading registered operation configurations...</p>
              )}
              {!configurationsPending && !configurationError && !configurations.length && (
                <p className="notice">
                  No configuration revisions are available. An organization administrator can
                  register external operation catalogs in{' '}
                  <Link to={`/w/${wid}?section=connections`}>Connection inventory</Link>.
                </p>
              )}
              {!configurationsPending && !!inputErrors.length && (
                <section className="notice error" role="alert" aria-label="Preparation validation">
                  <strong>Resolve these inputs before saving</strong>
                  <ul>
                    {inputErrors.map((message) => (
                      <li key={message}>{message}</li>
                    ))}
                  </ul>
                </section>
              )}
              <BoardEditor
                draft={draft}
                scenario={base.scenario}
                configurations={configurations}
                editable={!!canEdit}
                disabled={busy}
                onChange={setDraft}
              />
            </>
          )}
          {pane === 'preview' && (
            <>
              <ErrorNotice error={previews.error} />
              {previews.isPending && <p role="status">Loading immutable previews...</p>}
              {selectedPreview ? (
                <PreviewDetails preview={selectedPreview} />
              ) : previews.error || previewReferenceUnavailable || boardAccessDenied ? (
                <section className="glass preparation-panel">
                  <h2>Preview content is unavailable</h2>
                  <p>
                    Workspace or connection access may have changed. Cached configuration snapshots
                    are hidden. Your preparation draft and unsaved input have not been discarded.
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      void board.refetch()
                      void previews.refetch()
                    }}
                  >
                    Retry preview access
                  </button>
                </section>
              ) : (
                <section className="glass preparation-panel">
                  <h2>No frozen preview yet</h2>
                  <p>
                    Save the preparation draft, then freeze a preview of that exact saved version.
                    No target will be contacted.
                  </p>
                </section>
              )}
              {selectedPreview && (
                <section className="glass preparation-panel">
                  <h2>Review this preparation snapshot</h2>
                  <p className="preparation-note">
                    Approval requires an explicit workspace grant and a reviewer who did not create
                    or contribute to this board. Owner, editor, and administrator roles do not
                    confer approval.
                  </p>
                  {!canApprove && (
                    <div className="notice">
                      <strong>Preparation review is unavailable to this identity.</strong>
                      {contributor && (
                        <p>
                          You created or contributed to this board and cannot approve or reject its
                          preparation.
                        </p>
                      )}
                      <ul>
                        {(displayed.approval_blockers ?? []).map((reason) => (
                          <li key={reason}>{preparationReason(reason)}</li>
                        ))}
                      </ul>
                      <button
                        type="button"
                        disabled={board.isFetching || me.isFetching}
                        onClick={() => {
                          void board.refetch()
                          void me.refetch()
                        }}
                      >
                        Refresh review access
                      </button>
                    </div>
                  )}
                  {!previewCurrent && (
                    <p className="notice">
                      This is not the current saved preview. A fresh preview is required before a
                      decision.
                    </p>
                  )}
                  {dirty && (
                    <p className="notice">
                      Unsaved material changes cannot be approved. Save and freeze a new preview
                      first.
                    </p>
                  )}
                  <form
                    onSubmit={(event) => {
                      event.preventDefault()
                      if (!approvalDisabled) recordDecision.mutate()
                    }}
                  >
                    <fieldset className="preparation-inputs" disabled={approvalDisabled}>
                      <legend>Preparation-only decision</legend>
                      <p>
                        Reviewing preview {selectedPreview.sequence}, board version{' '}
                        {selectedPreview.board_version}.
                      </p>
                      <code className="preparation-digest">{selectedPreview.digest}</code>
                      <div className="preparation-fields">
                        <label>
                          Preparation decision
                          <select
                            required
                            value={decision}
                            onChange={(event) => {
                              const value = event.target.value
                              if (value === '' || value === 'approved' || value === 'rejected')
                                setDecision(value)
                            }}
                          >
                            <option value="">Choose a decision</option>
                            <option value="approved">Approve preparation</option>
                            <option value="rejected">Reject preparation</option>
                          </select>
                        </label>
                        <label>
                          Explicit approval expiry (local time)
                          <input
                            type="datetime-local"
                            required
                            value={expires}
                            onChange={(event) => setExpires(event.target.value)}
                          />
                        </label>
                        {expires && (
                          <p className="preparation-note full">
                            Proposed expiry in UTC: {timezoneDateTime(expires)}.
                            {!expiryValid && ' Choose a future expiry within thirty days.'}
                          </p>
                        )}
                        <label className="full">
                          Reviewer note
                          <textarea
                            required
                            maxLength={4000}
                            value={note}
                            onChange={(event) => setNote(event.target.value)}
                          />
                        </label>
                        <label className="check full">
                          <input
                            type="checkbox"
                            required
                            checked={acknowledged}
                            onChange={(event) => setAcknowledged(event.target.checked)}
                          />
                          I reviewed this exact snapshot and its missing prerequisites. Target
                          contents, connectivity, permissions, and delivery remain unverified.
                          Preparation approval is not authorization to execute.
                        </label>
                      </div>
                      <button
                        className="primary"
                        disabled={
                          !decision ||
                          !expiryValid ||
                          !note.trim() ||
                          !acknowledged ||
                          approvalDisabled
                        }
                      >
                        {recordDecision.isPending
                          ? 'Recording decision...'
                          : decision === 'rejected'
                            ? 'Reject preparation'
                            : 'Approve preparation'}
                      </button>
                    </fieldset>
                  </form>
                </section>
              )}
            </>
          )}
          {pane === 'history' && (
            <>
              <section className="glass preparation-panel">
                <h2>Immutable preview history</h2>
                <ErrorNotice error={previews.error} />
                {previews.isPending && <p role="status">Loading preview history...</p>}
                {availablePreviews?.length === 0 && <p>No previews have been frozen.</p>}
                {(previewReferenceUnavailable || previews.error) && (
                  <p className="notice">
                    Preview history is unavailable under current access. Previously cached
                    configuration content is not shown.
                  </p>
                )}
                <ul className="preparation-list">
                  {availablePreviews?.map((preview) => (
                    <li key={preview.id}>
                      <h3>
                        Preview {preview.sequence} · board version {preview.board_version}
                      </h3>
                      <span className="pill">
                        {preview.is_current ? 'Current saved version' : 'Historical snapshot'}
                      </span>
                      <code className="preparation-digest">{preview.digest}</code>
                      <p className="preparation-note">
                        {new Date(preview.created_at).toLocaleString()} ·{' '}
                        <code>{preview.created_by}</code>
                      </p>
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedPreviewId(preview.id)
                          setAcknowledged(false)
                          setPane('preview')
                        }}
                      >
                        Inspect preview {preview.sequence}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
              <ErrorNotice error={approvals.error} />
              {approvals.isPending ? (
                <p role="status">Loading preparation approval history...</p>
              ) : approvals.error ? (
                <p className="notice">
                  Decision history is unavailable. No empty or successful history is inferred.
                </p>
              ) : (
                <ApprovalHistory
                  approvals={approvals.data ?? []}
                  me={me.data}
                  now={now}
                  pending={busy || behind || !!board.error}
                  onRevoke={(approval) => revoke.mutate(approval)}
                />
              )}
            </>
          )}
        </div>
        <BoardProvenance board={displayed} />
      </div>
      <UnsavedChanges dirty={dirty || reviewDirty || busy} onExport={exportInput} />
    </main>
  )
}
