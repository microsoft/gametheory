import { useCallback, useEffect, useRef, useState } from 'react'
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useBlocker, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowUp,
  FileText,
  GitBranch,
  Image,
  MessageSquare,
  Save,
  Sparkles,
} from 'lucide-react'
import { ApiError, download, ErrorNotice, useSession } from './api'
import { DocumentEditor } from './DocumentEditor'
import { FlowEditor } from './FlowEditor'
import { AssetPanel } from './assets'
import type {
  Asset,
  Comment,
  Connection,
  Content,
  Environment,
  Planning,
  Revision,
  Scenario,
  Workspace,
} from './types'
import { normalizeScenario } from './types'
import { CreateBoardFromRevision, type BoardCreate } from './Boards'

type Pane = 'document' | 'flow' | 'assets' | 'review' | 'history'

export function Studio() {
  const { wid = '', sid = '' } = useParams()
  return <StudioContent key={`${wid}/${sid}`} wid={wid} sid={sid} />
}

export function StudioContent({ wid, sid }: { wid: string; sid: string }) {
  const { api, config } = useSession()
  const cache = useQueryClient()
  const uploading = useIsMutating({ mutationKey: [wid, 'upload'] }) > 0
  const path = `/workspaces/${wid}/scenarios/${sid}`
  const [base, setBase] = useState<Scenario>()
  const [content, setContent] = useState<Content>()
  const [pane, setPane] = useState<Pane>('document')
  const [prompt, setPrompt] = useState('')
  const [comment, setComment] = useState('')
  const [selectedAsset, setSelectedAsset] = useState<string>()
  const [review, setReview] = useState<Planning>()
  const [reviewBefore, setReviewBefore] = useState(false)
  const [notice, setNotice] = useState('')
  const [boardInputs, setBoardInputs] = useState<Record<number, BoardCreate>>({})
  const handleBoardInput = useCallback((revision: number, input: BoardCreate | null) => {
    setBoardInputs((previous) => {
      if (!input && !previous[revision]) return previous
      const next = { ...previous }
      if (input) next[revision] = input
      else delete next[revision]
      return next
    })
  }, [])
  const discardDialog = useRef<HTMLDialogElement>(null)
  const intent = useRef<{ id: string; prompt: string; version: number }>()
  const scenario = useQuery({
    queryKey: [wid, sid],
    queryFn: async () => normalizeScenario(await api.get<Scenario>(path)),
  })
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.get<Workspace[]>('/workspaces'),
  })
  const workspace = workspaces.data?.find((w) => w.id === wid)
  const conversations = useQuery({
    queryKey: [wid, sid, 'planning'],
    queryFn: () => api.get<Planning[]>(`${path}/planning`),
    refetchInterval: (query) =>
      query.state.data?.some((p) => p.status === 'queued' || p.status === 'running') ? 3000 : false,
  })
  const assets = useQuery({
    queryKey: [wid, 'assets'],
    queryFn: () => api.get<Asset[]>(`/workspaces/${wid}/assets`),
  })
  const connections = useQuery({
    queryKey: [wid, 'connections'],
    queryFn: () => api.get<Connection[]>(`/workspaces/${wid}/connections`),
  })
  const environments = useQuery({
    queryKey: ['environments'],
    queryFn: () => api.get<Environment[]>('/environments'),
  })
  const comments = useQuery({
    queryKey: [wid, sid, 'comments'],
    queryFn: () => api.get<Comment[]>(`${path}/comments`),
  })
  const revisions = useQuery({
    queryKey: [wid, sid, 'revisions'],
    queryFn: () => api.get<Revision[]>(`${path}/revisions`),
  })
  useEffect(() => {
    if (scenario.data && !base) {
      setBase(scenario.data)
      setContent(scenario.data.content)
    }
  }, [scenario.data, base])
  const dirty = !!base && !!content && JSON.stringify(base.content) !== JSON.stringify(content)
  const unsaved =
    dirty || !!prompt.trim() || !!comment.trim() || uploading || Object.keys(boardInputs).length > 0
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      unsaved && currentLocation.pathname !== nextLocation.pathname,
  )
  useEffect(() => {
    if (blocker.state === 'blocked') discardDialog.current?.showModal()
  }, [blocker.state])
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (unsaved) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [unsaved])
  function accepted(value: Scenario, submitted?: Content) {
    const normalized = normalizeScenario(value)
    setBase(normalized)
    setContent((latest) =>
      submitted && JSON.stringify(latest) !== JSON.stringify(submitted)
        ? latest
        : normalized.content,
    )
    cache.setQueryData([wid, sid], normalized)
    void cache.invalidateQueries({ queryKey: [wid, 'scenarios'] })
  }
  const save = useMutation({
    mutationFn: (snapshot: { content: Content; version: number }) =>
      api.send<Scenario>(path, 'PUT', snapshot.content, snapshot.version),
    onSuccess: (value, snapshot) => {
      accepted(value, snapshot.content)
      setNotice('Draft saved.')
    },
  })
  const reload = useMutation({
    mutationFn: () => api.get<Scenario>(path),
    onSuccess: (value) => {
      accepted(value)
      save.reset()
      decision.reset()
      setNotice('Loaded the latest saved draft.')
    },
  })
  const publish = useMutation({
    mutationFn: () =>
      api.send<{ version: number }>(`${path}/revisions`, 'POST', undefined, base?.version),
    onSuccess: (value) => {
      void cache.invalidateQueries({ queryKey: [wid, sid, 'revisions'] })
      setNotice(`Revision ${value.version} published. This does not approve or start execution.`)
    },
  })
  const planning = useMutation({
    mutationFn: () => {
      if (!base) throw new Error('Load a scenario first')
      if (
        !intent.current ||
        intent.current.prompt !== prompt ||
        intent.current.version !== base.version
      ) {
        intent.current = { id: crypto.randomUUID(), prompt, version: base.version }
      }
      return api.send(`${path}/planning`, 'POST', {
        prompt,
        base_version: base.version,
        request_id: intent.current.id,
      })
    },
    onSuccess: () => {
      setPrompt('')
      intent.current = undefined
      void cache.invalidateQueries({ queryKey: [wid, sid, 'planning'] })
    },
  })
  const decision = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'apply' | 'reject' }) =>
      api.send<{ scenario: Scenario | null }>(
        `${path}/planning/${id}/${action}`,
        'POST',
        undefined,
        base?.version,
      ),
    onSuccess: (value) => {
      if (value.scenario) accepted(value.scenario)
      void cache.invalidateQueries({ queryKey: [wid, sid, 'planning'] })
      setReview(undefined)
      setPane('document')
      setNotice(
        value.scenario
          ? 'Proposal applied and saved as a new draft version.'
          : 'Proposal rejected. The draft was not changed.',
      )
    },
  })
  const addComment = useMutation({
    mutationFn: () =>
      api.send(`${path}/comments`, 'POST', { body: comment, base_version: base?.version }),
    onSuccess: () => {
      setComment('')
      void cache.invalidateQueries({ queryKey: [wid, sid, 'comments'] })
    },
  })
  const busy =
    save.isPending || reload.isPending || publish.isPending || decision.isPending || uploading
  const editable = !!workspace && workspace.role !== 'viewer' && !busy
  const conflict =
    (save.error instanceof ApiError && save.error.status === 409) ||
    (decision.error instanceof ApiError && decision.error.status === 409)
  const error =
    scenario.error ??
    workspaces.error ??
    assets.error ??
    connections.error ??
    environments.error ??
    conversations.error ??
    save.error ??
    publish.error ??
    decision.error ??
    reload.error
  if (!base || !content)
    return (
      <main className="page">
        <Link to={`/w/${wid}`}>Back to workspace</Link>
        <ErrorNotice error={error} />
        {!error && <p role="status">Opening scenario studio...</p>}
      </main>
    )
  const active = conversations.data?.some((p) => ['queued', 'running'].includes(p.status))
  function exportDraft() {
    download(
      new Blob([JSON.stringify(content, null, 2)], { type: 'application/json' }),
      'scenario.json',
    )
  }
  return (
    <div className="studio">
      <div className="studio-top">
        <div className="studio-heading">
          <Link className="breadcrumb" to={`/w/${wid}`}>
            <ArrowLeft size={14} />
            {workspace?.name ?? 'Workspace'}
          </Link>
          <h1>{base.content.title}</h1>
          <span className="pill">Draft {base.version}</span>
          <span className={dirty ? 'status unsaved' : 'status'}>
            {dirty ? 'Unsaved changes' : 'Saved draft'}
          </span>
        </div>
        <div className="toolbar">
          <button disabled={busy} onClick={exportDraft}>
            Export JSON
          </button>
          <button
            disabled={!editable || !dirty}
            onClick={() => save.mutate({ content, version: base.version })}
          >
            <Save size={15} />
            {save.isPending ? 'Saving...' : 'Save draft'}
          </button>
          <button
            className="primary"
            disabled={!editable || dirty}
            onClick={() => publish.mutate()}
          >
            Publish revision
          </button>
        </div>
      </div>
      <div className="context-strip">
        <span className="eyebrow">PLANNING CONTEXT</span>
        <span>{content.objectives.length} objectives</span>
        <span>{content.nodes.length} steps</span>
        <span>{content.asset_ids.length} pinned assets</span>
        <span className="scope-label">Authoring only · No external actions</span>
      </div>
      <ErrorNotice error={error} />
      {conflict && (
        <div className="notice">
          <strong>A newer draft exists.</strong> Your input is still here. Export it before loading
          the latest version; stale proposals cannot overwrite newer work.
          <div className="toolbar">
            <button onClick={exportDraft}>Export my draft</button>
            <button
              onClick={() => {
                if (window.confirm('Discard local edits and load the latest saved draft?'))
                  reload.mutate()
              }}
            >
              Load latest draft
            </button>
          </div>
        </div>
      )}
      {notice && (
        <div className="notice success" role="status">
          {notice}
          <button
            className="dismiss"
            aria-label="Dismiss notification"
            onClick={() => setNotice('')}
          >
            ×
          </button>
        </div>
      )}
      <div className="studio-grid">
        <aside className="glass conversation">
          <div className="conversation-heading">
            <span className="assistant-icon">
              <Sparkles size={18} />
            </span>
            <div>
              <h2>Planning partner</h2>
              <p>Explore. Propose. Review.</p>
            </div>
          </div>
          <div className="chat-messages">
            <div className="assistant-intro">
              <span className="eyebrow">YOU SET THE DIRECTION</span>
              <h3>What should this scenario help you learn?</h3>
              <p>
                I can help shape objectives, refine the narrative, and propose branches. You decide
                what becomes part of the plan.
              </p>
            </div>
            {conversations.data?.map((item) => (
              <div className="conversation-turn" key={item.id}>
                <div className="user-message">
                  <span className="eyebrow">SCENARIO OWNER · DRAFT {item.base_version}</span>
                  <p>{item.prompt}</p>
                </div>
                <div className="assistant-message">
                  <span className={`pill ${item.status === 'failed' ? 'danger' : ''}`}>
                    {item.status}
                  </span>
                  {item.error && <p className="error-text">{item.error}</p>}
                  {['queued', 'running'].includes(item.status) && (
                    <p role="status">
                      Planning request is {item.status}. You can leave this page; its state is
                      stored.
                    </p>
                  )}
                  {item.proposal && (
                    <>
                      <p>{item.proposal.summary}</p>
                      {item.status === 'proposed' && (
                        <>
                          <button
                            onClick={() => {
                              setReview(item)
                              setReviewBefore(false)
                              setPane('review')
                            }}
                          >
                            Review proposed changes
                          </button>
                          {item.base_version !== base.version && (
                            <p className="error-text">
                              Based on an older draft. Request a new proposal before applying.
                            </p>
                          )}
                        </>
                      )}
                    </>
                  )}
                  {item.status === 'failed' && (
                    <button disabled={!editable || !!prompt} onClick={() => setPrompt(item.prompt)}>
                      Use prompt for a new request
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
          <form
            className="chat-composer"
            onSubmit={(e) => {
              e.preventDefault()
              planning.mutate()
            }}
          >
            <div className="composer-context">
              <FileText size={14} />
              <span>Current saved plan · draft {base.version}</span>
            </div>
            <label className="sr-only" htmlFor="planning-prompt">
              Planning message
            </label>
            <textarea
              id="planning-prompt"
              value={prompt}
              maxLength={8000}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe an objective or a change..."
              disabled={!editable || planning.isPending}
            />
            <ErrorNotice error={planning.error} />
            {!config.capabilities.planning ? (
              <p className="muted">AI planning is not configured. Manual authoring is available.</p>
            ) : dirty ? (
              <p className="muted">Save your changes before requesting a proposal.</p>
            ) : (
              <p className="muted">
                Proposals never run actions or change the draft automatically.
              </p>
            )}
            <button
              className="primary"
              disabled={
                !editable ||
                !prompt.trim() ||
                !config.capabilities.planning ||
                dirty ||
                planning.isPending ||
                active
              }
            >
              <ArrowUp size={16} />
              {planning.isPending ? 'Submitting...' : 'Request proposal'}
            </button>
          </form>
        </aside>
        <section className="glass plan-pane">
          <nav className="tabs pane-tabs" aria-label="Scenario editors">
            <button aria-current={pane === 'document'} onClick={() => setPane('document')}>
              <FileText size={15} />
              Plan
            </button>
            <button aria-current={pane === 'flow'} onClick={() => setPane('flow')}>
              <GitBranch size={15} />
              Flow
            </button>
            <button aria-current={pane === 'assets'} onClick={() => setPane('assets')}>
              <Image size={15} />
              Assets
            </button>
            <button aria-current={pane === 'history'} onClick={() => setPane('history')}>
              <MessageSquare size={15} />
              Review & history
            </button>
            {review && (
              <button aria-current={pane === 'review'} onClick={() => setPane('review')}>
                Proposal
              </button>
            )}
          </nav>
          <div className="plan-scroll">
            {pane === 'document' && (
              <>
                <span className="eyebrow">SCENARIO PLAN</span>
                <label className="title-field">
                  <span className="sr-only">Scenario title</span>
                  <input
                    value={content.title}
                    maxLength={160}
                    disabled={!editable}
                    onChange={(e) => setContent({ ...content, title: e.target.value })}
                  />
                </label>
                <div className="document-meta">
                  <span className="pill">Shared draft</span>
                  <span>Published versions preserve their exact inputs.</span>
                </div>
                <DocumentEditor
                  wid={wid}
                  assets={assets.data ?? []}
                  content={content}
                  editable={editable}
                  onChange={(document) =>
                    setContent((previous) => (previous ? { ...previous, document } : previous))
                  }
                  onReference={(kind, target) => {
                    if (kind === 'asset') {
                      setSelectedAsset(target)
                      setPane('assets')
                    } else setPane('flow')
                  }}
                />
                <div className="embedded-flow">
                  <div>
                    <span className="eyebrow">EMBEDDED FLOW</span>
                    <h3>
                      {content.nodes.length
                        ? `${content.nodes.length} steps, ${content.edges.length} branches`
                        : 'Shape the path through your scenario'}
                    </h3>
                    <div className="mini-flow">
                      {content.nodes.slice(0, 4).map((n) => (
                        <span key={n.id}>{n.label}</span>
                      ))}
                    </div>
                  </div>
                  <button onClick={() => setPane('flow')}>
                    Open flow editor <GitBranch size={15} />
                  </button>
                </div>
                <section className="objectives">
                  <div className="section-heading">
                    <div>
                      <span className="eyebrow">DEFINE SUCCESS BEFORE THE RUN</span>
                      <h2>Objectives & evidence criteria</h2>
                    </div>
                    <button
                      disabled={!editable}
                      onClick={() =>
                        setContent({
                          ...content,
                          objectives: [
                            ...content.objectives,
                            {
                              id: crypto.randomUUID(),
                              title: 'New objective',
                              criterion: 'Describe the evidence required to assess this objective.',
                            },
                          ],
                        })
                      }
                    >
                      Add objective
                    </button>
                  </div>
                  {content.objectives.map((objective, index) => (
                    <fieldset className="objective-card" disabled={!editable} key={objective.id}>
                      <legend>Objective {index + 1}</legend>
                      <label>
                        Objective
                        <input
                          maxLength={160}
                          value={objective.title}
                          onChange={(e) =>
                            setContent({
                              ...content,
                              objectives: content.objectives.map((o) =>
                                o.id === objective.id ? { ...o, title: e.target.value } : o,
                              ),
                            })
                          }
                        />
                      </label>
                      <label>
                        Measurable evidence criterion
                        <textarea
                          maxLength={2000}
                          value={objective.criterion}
                          onChange={(e) =>
                            setContent({
                              ...content,
                              objectives: content.objectives.map((o) =>
                                o.id === objective.id ? { ...o, criterion: e.target.value } : o,
                              ),
                            })
                          }
                        />
                      </label>
                      <button
                        onClick={() =>
                          setContent({
                            ...content,
                            objectives: content.objectives.filter((o) => o.id !== objective.id),
                          })
                        }
                      >
                        Remove objective
                      </button>
                    </fieldset>
                  ))}
                </section>
              </>
            )}
            {pane === 'flow' && (
              <FlowEditor
                content={content}
                editable={editable}
                connections={connections.data ?? []}
                environments={environments.data ?? []}
                onChange={setContent}
              />
            )}
            {pane === 'assets' && (
              <AssetPanel
                wid={wid}
                assets={assets.data ?? []}
                content={content}
                editable={editable && config.capabilities.assets}
                selected={selectedAsset}
                onSelect={setSelectedAsset}
                onChange={setContent}
                onUploaded={(id) =>
                  setContent((latest) =>
                    latest
                      ? {
                          ...latest,
                          asset_ids: [...new Set([...latest.asset_ids, id])],
                        }
                      : latest,
                  )
                }
              />
            )}
            {pane === 'review' && review?.proposal && (
              <div className="proposal-review">
                <span className="eyebrow">PROPOSED CHANGE · BASE DRAFT {review.base_version}</span>
                <h2>{review.proposal.summary}</h2>
                <div className="notice">
                  This replaces the scenario content with the reviewed proposal. Permissions,
                  inventory, and external systems are not changed.
                </div>
                <div className="toolbar">
                  <button aria-pressed={reviewBefore} onClick={() => setReviewBefore(true)}>
                    Current draft
                  </button>
                  <button aria-pressed={!reviewBefore} onClick={() => setReviewBefore(false)}>
                    Proposed draft
                  </button>
                </div>
                <h3>{reviewBefore ? content.title : review.proposal.content.title}</h3>
                <DocumentEditor
                  wid={wid}
                  assets={assets.data ?? []}
                  key={`${review.id}-${reviewBefore}`}
                  content={reviewBefore ? content : review.proposal.content}
                  editable={false}
                  onChange={() => undefined}
                  onReference={() => undefined}
                />
                <details open>
                  <summary>Review all structured changes</summary>
                  <pre>
                    {JSON.stringify(reviewBefore ? content : review.proposal.content, null, 2)}
                  </pre>
                </details>
                {dirty && (
                  <p className="error-text">
                    Save or explicitly discard your edits before applying a proposal.
                  </p>
                )}
                {review.base_version !== base.version && (
                  <p className="error-text">This proposal is stale and cannot be applied.</p>
                )}
                <div className="toolbar">
                  <button
                    disabled={!editable || decision.isPending}
                    onClick={() => decision.mutate({ id: review.id, action: 'reject' })}
                  >
                    Reject proposal
                  </button>
                  <button
                    className="primary"
                    disabled={!editable || dirty || review.base_version !== base.version}
                    onClick={() => decision.mutate({ id: review.id, action: 'apply' })}
                  >
                    Apply reviewed proposal
                  </button>
                </div>
              </div>
            )}
            {pane === 'history' && (
              <section className="stack">
                <span className="eyebrow">SHARED REVIEW</span>
                <h2>Comments</h2>
                <ErrorNotice error={comments.error ?? revisions.error ?? addComment.error} />
                {comments.data?.map((item) => (
                  <article className="comment" key={item.id}>
                    <span className="eyebrow">
                      DRAFT {item.base_version} · {new Date(item.created_at).toLocaleString()}
                    </span>
                    <p>{item.body}</p>
                    <small>Author: {item.actor}</small>
                  </article>
                ))}
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    addComment.mutate()
                  }}
                >
                  <label>
                    Comment on saved draft {base.version}
                    <textarea
                      required
                      maxLength={5000}
                      value={comment}
                      disabled={!editable || addComment.isPending}
                      onChange={(e) => setComment(e.target.value)}
                    />
                  </label>
                  <button disabled={!editable || !comment.trim() || addComment.isPending}>
                    Add comment
                  </button>
                </form>
                <h2>Published revisions</h2>
                <p className="muted">
                  Publication freezes authoring inputs; it is not execution approval.
                </p>
                {revisions.data?.map((revision) => (
                  <details key={revision.version}>
                    <summary>
                      Revision {revision.version} · {new Date(revision.created_at).toLocaleString()}
                    </summary>
                    <p>{revision.content.title}</p>
                    <button
                      onClick={() =>
                        download(
                          new Blob([JSON.stringify(revision.content, null, 2)], {
                            type: 'application/json',
                          }),
                          `scenario-revision-${revision.version}.json`,
                        )
                      }
                    >
                      Download immutable snapshot
                    </button>
                    {workspace?.role !== 'viewer' && workspace && (
                      <CreateBoardFromRevision
                        wid={wid}
                        scenarioId={sid}
                        revisionVersion={revision.version}
                        title={revision.content.title}
                        disabled={!editable}
                        onInputChange={handleBoardInput}
                        initialInput={boardInputs[revision.version]}
                      />
                    )}
                    <pre>{JSON.stringify(revision.content, null, 2)}</pre>
                  </details>
                ))}
              </section>
            )}
          </div>
        </section>
      </div>
      {blocker.state === 'blocked' && (
        <dialog
          ref={discardDialog}
          className="glass modal"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="discard-heading"
          onCancel={(event) => {
            event.preventDefault()
            blocker.reset()
          }}
        >
          <h2 id="discard-heading">Leave unsaved work?</h2>
          <p>
            Your unsaved plan, comment, planning message, or board creation input will be discarded.
          </p>
          <div className="toolbar">
            <button autoFocus onClick={() => blocker.reset()}>
              Keep editing
            </button>
            {Object.keys(boardInputs).length > 0 && (
              <button
                onClick={() =>
                  download(
                    new Blob([JSON.stringify(Object.values(boardInputs), null, 2)], {
                      type: 'application/json',
                    }),
                    'board-creation-inputs.json',
                  )
                }
              >
                Export board creation input
              </button>
            )}
            <button className="danger" onClick={() => blocker.proceed()}>
              Discard and leave
            </button>
          </div>
        </dialog>
      )}
    </div>
  )
}
