import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import type { components } from './api.generated'
import { ApiError, ErrorNotice, useSession } from './api'
import { exportJson, UnsavedChanges } from './PreparationShared'
import { timezoneDateTime, type Me, type Preview } from './preparation'
import { parseUniqueJson } from './strictJson'

type Schemas = components['schemas']
type Run = Schemas['RunView']
type Action = Schemas['RunControl']['action']
const emptyBindings = '{\n  "observations": [],\n  "objectives": [],\n  "recovery": []\n}'
export type RunSetupInput = { dirty: boolean; trigger: 'manual' | 'scheduled'; options: string }
export const emptyRunSetup: RunSetupInput = {
  dirty: false,
  trigger: 'manual',
  options: emptyBindings,
}

export function runOptions(text: string): Record<string, unknown> {
  const value = parseUniqueJson(text)
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw new Error('Run bindings must be a JSON object.')
  const result: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(value)) {
    if (!['observations', 'objectives', 'recovery'].includes(key) || !Array.isArray(item))
      throw new Error('Only observations, objectives, and recovery arrays are accepted.')
    result[key] = item
  }
  return result
}

export function BoardRuns({
  wid,
  bid,
  version,
  preview,
  unsaved,
  onInputChange,
}: {
  wid: string
  bid: string
  version: number
  preview?: Preview
  unsaved: boolean
  onInputChange?: (input: RunSetupInput) => void
}) {
  const { api } = useSession()
  const cache = useQueryClient()
  const [trigger, setTrigger] = useState<'manual' | 'scheduled'>('manual')
  const [options, setOptions] = useState(emptyBindings)
  const [createdId, setCreatedId] = useState('')
  const path = `/workspaces/${wid}/boards/${bid}/runs`
  const runs = useQuery({
    queryKey: [wid, 'board-runs', bid],
    queryFn: () => api.get<Schemas['RunSummary'][]>(path),
    refetchInterval: 10000,
  })
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get<Me>('/me') })
  const grants = useQuery({
    queryKey: [wid, 'execution-grants'],
    queryFn: () => api.get<Schemas['ExecutionGrantView'][]>(`/workspaces/${wid}/execution-grants`),
  })
  const operator =
    !grants.error &&
    !me.error &&
    grants.data?.some(
      (item) => item.capability === 'operator' && item.object_id === me.data?.object_id,
    )
  const create = useMutation({
    mutationFn: () => {
      if (!preview || !preview.is_current || unsaved)
        throw new Error('Save and freeze the exact preparation before creating a run.')
      return api.send<Run>(
        path,
        'POST',
        {
          ...runOptions(options),
          preview_id: preview.id,
          preview_digest: preview.digest,
          trigger,
        },
        version,
      )
    },
    onSuccess: (run) => {
      setOptions(emptyBindings)
      setTrigger('manual')
      setCreatedId(run.id)
      void cache.invalidateQueries({ queryKey: [wid, 'board-runs', bid] })
    },
  })
  useEffect(() => {
    onInputChange?.({
      dirty: !createdId && (options !== emptyBindings || trigger !== 'manual' || create.isPending),
      options,
      trigger,
    })
  }, [options, trigger, create.isPending, createdId, onInputChange])
  useEffect(() => () => onInputChange?.(emptyRunSetup), [onInputChange])
  return (
    <>
      <section className="glass preparation-panel">
        <h2>Exercise runs</h2>
        <p>
          Run a pinned preparation through the isolated SQL/REST executor. Production always
          requires independent approval; other environments follow their administrator policy.
        </p>
        <ErrorNotice error={runs.error ?? grants.error ?? me.error ?? create.error} />
        {createdId && (
          <p className="notice" role="status">
            Run frozen.{' '}
            <Link to={`/w/${wid}/runs/${createdId}`}>
              Review its policy, readiness, and execution controls
            </Link>
            .
          </p>
        )}
        {!operator && grants.data && (
          <p className="notice">
            An administrator must grant you explicit operator access before you can create a run.
            Authoring and preparation approval do not grant execution rights.
          </p>
        )}
        {(!preview?.is_current || unsaved) && (
          <p className="notice">
            Save the preparation and freeze a current preview before creating a run.
          </p>
        )}
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault()
            create.mutate()
          }}
        >
          <label>
            Start mode
            <select
              value={trigger}
              disabled={create.isPending}
              onChange={(event) => {
                setCreatedId('')
                if (event.target.value === 'manual' || event.target.value === 'scheduled')
                  setTrigger(event.target.value)
              }}
            >
              <option value="manual">Manual, inside the pinned window</option>
              <option value="scheduled">One-off, at the pinned window start</option>
            </select>
          </label>
          <details>
            <summary>Observation, objective, and recovery bindings</summary>
            <p>
              Optional, restricted JSON bindings are validated by the service before the run is
              created. Use step and objective IDs from the pinned preview. No scripts or arbitrary
              requests are accepted. Unbound objectives remain indeterminate; unbound write recovery
              requires an external operator.
            </p>
            <label>
              Run bindings JSON
              <textarea
                className="run-json-input"
                value={options}
                disabled={create.isPending}
                onChange={(event) => {
                  setCreatedId('')
                  setOptions(event.target.value)
                }}
                spellCheck={false}
              />
            </label>
            <p>
              Each observation identifies a read step, field, comparison, interval, timeout, and
              sample limit. Objective rules identify an objective, evidence step/field, comparison,
              and optional authoritative timing anchor. Recovery bindings require recorded ownership
              and version preconditions.
            </p>
            {preview && (
              <button
                type="button"
                onClick={() => exportJson(preview.manifest, 'pinned-preparation.json')}
              >
                Export pinned IDs and contracts
              </button>
            )}
          </details>
          <p className="preparation-note">
            Creating a run does not dispatch it. Review its exact manifest, readiness, and
            environment policy next. SQL idempotency keys are dispatcher-owned; clear any
            preparation literal for that field.
          </p>
          <button
            className="primary"
            disabled={!operator || !preview?.is_current || unsaved || create.isPending}
          >
            {create.isPending ? 'Freezing run...' : 'Create pinned run'}
          </button>
        </form>
      </section>
      <section className="glass preparation-panel">
        <h2>Run history</h2>
        {runs.isPending && <p role="status">Loading runs...</p>}
        {!runs.error && runs.data?.length === 0 && <p>No runs have been created for this board.</p>}
        <ul className="preparation-list">
          {!runs.error &&
            runs.data?.map((run) => (
              <li key={run.id}>
                <Link to={`/w/${wid}/runs/${run.id}`}>Run {run.id.slice(0, 8)}</Link>
                <span className="pill">{run.state.replaceAll('_', ' ')}</span>
                <span>
                  {run.phase} · {new Date(run.created_at).toLocaleString()}
                </span>
                <span>Open the run to review its current policy and evidence.</span>
              </li>
            ))}
        </ul>
      </section>
    </>
  )
}

export function ExerciseRun() {
  const { wid = '', rid = '' } = useParams()
  return <ExerciseRunContent key={`${wid}/${rid}`} wid={wid} rid={rid} />
}

export function ExerciseRunContent({ wid, rid }: { wid: string; rid: string }) {
  const { api } = useSession()
  const cache = useQueryClient()
  const path = `/workspaces/${wid}/runs/${rid}`
  const key = ['run', wid, rid]
  const [note, setNote] = useState('')
  const [decision, setDecision] = useState<'approved' | 'rejected' | ''>('')
  const [expires, setExpires] = useState('')
  const [reviewBase, setReviewBase] = useState<{
    version: number
    context_id: string
    manifest_digest: string
  }>()
  const [manualStep, setManualStep] = useState('')
  const [evidence, setEvidence] = useState('')
  const [notice, setNotice] = useState('')
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get<Me>('/me') })
  const query = useQuery({
    queryKey: key,
    queryFn: async () => {
      const value = await api.read<Run>(path)
      if (value.data.version !== value.version)
        throw new Error('Run versions disagree. Actions are unavailable.')
      return value.data
    },
    refetchInterval: 5000,
  })
  async function accepted(value: Run) {
    cache.setQueryData(key, value)
    setNote('')
    setNotice(
      'Action recorded. The timeline distinguishes accepted, completed, and uncertain effects.',
    )
    await cache.invalidateQueries({ queryKey: [wid, 'board-runs', value.board_id] })
  }
  const control = useMutation({
    mutationFn: (action: Action) => {
      if (!query.data || query.error || !note.trim())
        throw new Error('Load the current run and enter an operator note.')
      return api.send<Run>(
        `${path}/controls`,
        'POST',
        { action, note: note.trim() },
        query.data.version,
      )
    },
    onSuccess: accepted,
  })
  const review = useMutation({
    mutationFn: () => {
      if (!query.data || query.error || !reviewBase || !decision || !note.trim())
        throw new Error('Select the exact review context, decision, expiry, and note.')
      return api.send<Run>(
        `${path}/approvals`,
        'POST',
        {
          context_id: reviewBase.context_id,
          manifest_digest: reviewBase.manifest_digest,
          decision,
          expires_at: timezoneDateTime(expires),
          note: note.trim(),
        },
        reviewBase.version,
      )
    },
    onSuccess: async (value) => {
      setDecision('')
      setExpires('')
      setReviewBase(undefined)
      await accepted(value)
    },
  })
  const revoke = useMutation({
    mutationFn: (approvalId: string) => {
      if (!query.data || query.error)
        throw new Error('Load the current run before revoking a review.')
      return api.send<Run>(
        `${path}/approvals/${approvalId}/revoke`,
        'POST',
        undefined,
        query.data.version,
      )
    },
    onSuccess: accepted,
  })
  const report = useMutation({
    mutationFn: () => {
      const run = query.data
      const step = run?.steps.find((item) => `${item.phase}/${item.step_id}` === manualStep)
      if (!run || query.error || !step || !note.trim() || !evidence.trim())
        throw new Error('Select the exact item and supply an external evidence reference and note.')
      return api.send<Run>(
        `${path}/manual-recovery-reports`,
        'POST',
        {
          step_id: step.step_id,
          phase: step.phase,
          evidence_reference: evidence.trim(),
          note: note.trim(),
        },
        run.version,
      )
    },
    onSuccess: async (value) => {
      setManualStep('')
      setEvidence('')
      await accepted(value)
    },
  })
  const run = query.data
  const busy = control.isPending || review.isPending || revoke.isPending || report.isPending
  const error = query.error ?? control.error ?? review.error ?? revoke.error ?? report.error
  const exportInput = () =>
    exportJson(
      { note, decision, expires, reviewBase, manualStep, evidence },
      'run-local-input.json',
    )
  if (!run || query.error)
    return (
      <main className="page">
        <Link to={`/w/${wid}?section=boards`}>Back to game boards</Link>
        <h1>Exercise run</h1>
        <ErrorNotice error={error} />
        {query.isPending ? (
          <p role="status">Loading run...</p>
        ) : (
          <button onClick={() => void query.refetch()}>Retry run access</button>
        )}
        <UnsavedChanges dirty={!!note || !!decision || !!evidence || busy} onExport={exportInput} />
      </main>
    )
  const canAct = run.can_operate && !busy && !!note.trim()
  const canSafetyAct = run.can_stop && !busy && !!note.trim()
  const active = [
    'queued',
    'scheduled',
    'running',
    'waiting',
    'paused',
    'intervention',
    'stopping',
  ].includes(run.state)
  const phaseLabel = run.phase === 'recovery' ? 'recovery' : 'run'
  const reviewChanged = !!reviewBase && reviewBase.version !== run.version
  const manualItems = run.steps.filter(
    (step) =>
      step.state === 'manual_required' ||
      (step.state === 'unknown' && run.state === 'stopped_incomplete'),
  )
  function pinReview() {
    if (run?.context_id)
      setReviewBase({
        version: run.version,
        context_id: run.context_id,
        manifest_digest: run.manifest_digest,
      })
  }
  return (
    <main className="page">
      <Link className="breadcrumb" to={`/w/${wid}/boards/${run.board_id}`}>
        Back to preparation board
      </Link>
      <div className="page-heading">
        <div>
          <h1>{run.phase === 'recovery' ? 'Exercise recovery' : 'Exercise run'}</h1>
          <p>
            {run.manifest.preparation.draft.name} · {run.id.slice(0, 8)}
          </p>
        </div>
        <span className="pill run-state" role="status" aria-live="polite">
          {run.state.replaceAll('_', ' ')}
        </span>
      </div>
      <ErrorNotice error={error} />
      {error instanceof ApiError && error.status === 409 && (
        <p className="notice">
          The action was not applied. Your input is retained.{' '}
          <button onClick={() => void query.refetch()}>Refresh run, keep input</button>
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <div className="run-workspace">
        <section className="stack">
          <section className="glass preparation-panel">
            <h2>Execution authority</h2>
            <strong>
              {run.approval_status === 'unresolved'
                ? 'Environment approval policy is unresolved'
                : run.approval_required
                  ? `Independent approval ${run.approval_status.replaceAll('_', ' ')}`
                  : 'Approval not required by environment policy'}
            </strong>
            <p>
              Preparation review is separate. Every external action rechecks current policies,
              access, readiness, and the pinned window.
            </p>
            <dl className="preparation-meta">
              <dt>Operator</dt>
              <dd>
                <code>{run.operator}</code>
              </dd>
              <dt>Run version</dt>
              <dd>{run.version}</dd>
              <dt>Manifest digest</dt>
              <dd>
                <code className="preparation-digest">{run.manifest_digest}</code>
              </dd>
            </dl>
            {run.authorization && (
              <details>
                <summary>Policies, resolved identities, and readiness receipts</summary>
                <p>
                  Authorization requested by <code>{run.authorization.created_by}</code> at{' '}
                  {new Date(run.authorization.created_at).toLocaleString()}. These are the pinned
                  records, not a claim that this page performed live probes.
                </p>
                <ul className="preparation-list">
                  {run.authorization.policies.map((policy) => (
                    <li key={policy.environment_id}>
                      <strong>
                        {policy.name} · policy version {policy.version}
                      </strong>
                      <span>
                        {policy.classification} ·{' '}
                        {policy.approval_required
                          ? 'Independent approval required'
                          : 'Approval not required'}
                      </span>
                    </li>
                  ))}
                </ul>
                <h3>Resolved target identities</h3>
                <pre className="run-evidence">
                  {JSON.stringify(run.authorization.targets, null, 2)}
                </pre>
                <h3>Operator-attested readiness</h3>
                <ul className="preparation-list">
                  {run.authorization.readiness.map((receipt) => (
                    <li key={receipt.id}>
                      <code>{receipt.id}</code>
                      <span>{receipt.evidence_reference}</span>
                      <span>
                        Checked {new Date(receipt.checked_at).toLocaleString()}; expires{' '}
                        {new Date(receipt.expires_at).toLocaleString()}
                      </span>
                      <small>Attested by {receipt.operator}</small>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {run.blockers.length > 0 && (
              <div className="notice">
                <strong>Current blockers</strong>
                <ul>
                  {run.blockers.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            )}
            <details>
              <summary>Pinned execution manifest</summary>
              <p>
                Historical inputs never change with subsequent drafts, assets, or environment
                settings.
              </p>
              <button onClick={() => exportJson(run.manifest, `run-${run.id}-manifest.json`)}>
                Export manifest
              </button>
              <pre className="run-evidence">{JSON.stringify(run.manifest, null, 2)}</pre>
            </details>
          </section>
          <section className="glass preparation-panel">
            <h2>Step progress</h2>
            <ol className="run-timeline">
              {run.steps
                .filter((step) => step.phase === run.phase)
                .map((step) => (
                  <li key={step.step_id}>
                    <strong>
                      {run.manifest.preparation.draft.steps?.find(
                        (item) => item.id === step.step_id,
                      )?.label ?? step.step_id}
                    </strong>
                    <span className="pill">{step.state.replaceAll('_', ' ')}</span>
                    <span>{step.samples} recorded attempts</span>
                    {step.reason && <p>{step.reason}</p>}
                    {Object.keys(step.result).length > 0 && (
                      <details>
                        <summary>Recorded results</summary>
                        <pre className="run-evidence">{JSON.stringify(step.result, null, 2)}</pre>
                      </details>
                    )}
                  </li>
                ))}
            </ol>
          </section>
          <section className="glass preparation-panel">
            <h2>Objective assessment</h2>
            <p>
              Missing or incomplete evidence stays indeterminate. A source timestamp is not proof
              that someone observed it on time.
            </p>
            <ul className="preparation-list">
              {run.findings.map((finding) => (
                <li key={finding.objective_id}>
                  <strong>
                    {run.manifest.preparation.scenario.content.objectives?.find(
                      (item) => item.id === finding.objective_id,
                    )?.title ?? finding.objective_id}
                  </strong>
                  <span className="pill">{finding.state}</span>
                  <p>{finding.reason}</p>
                  <small>{finding.evidence_ids.length} linked evidence records</small>
                </li>
              ))}
            </ul>
            {run.findings.length === 0 && (
              <p>No objectives were included in the pinned scenario.</p>
            )}
          </section>
          <section className="glass preparation-panel">
            <h2>Evidence timeline</h2>
            <ol className="run-timeline">
              {run.events.map((event) => (
                <li key={event.id}>
                  <strong>{event.kind.replaceAll('.', ' ').replaceAll('_', ' ')}</strong>
                  <time dateTime={event.created_at}>
                    {new Date(event.created_at).toLocaleString()}
                  </time>
                  <code>{event.id}</code>
                  <details>
                    <summary>Evidence details</summary>
                    <pre className="run-evidence">{JSON.stringify(event.detail, null, 2)}</pre>
                  </details>
                  {event.kind === 'run.reviewed' &&
                    typeof event.detail.approval_id === 'string' &&
                    (me.data?.organization_admin ||
                      event.detail.reviewer === me.data?.object_id) && (
                      <button
                        disabled={busy}
                        onClick={() => {
                          if (
                            typeof event.detail.approval_id === 'string' &&
                            window.confirm(
                              'Revoke this execution approval? Future actions may be blocked.',
                            )
                          )
                            revoke.mutate(event.detail.approval_id)
                        }}
                      >
                        Revoke execution approval
                      </button>
                    )}
                </li>
              ))}
            </ol>
          </section>
        </section>
        <aside className="stack" aria-label="Run actions">
          <section className="glass preparation-panel">
            <h2>{run.can_stop ? 'Operator controls' : 'Read-only run access'}</h2>
            <label>
              Operator / reviewer note
              <textarea
                value={note}
                maxLength={2000}
                disabled={busy}
                onChange={(event) => setNote(event.target.value)}
              />
            </label>
            {run.can_stop && (
              <div className="run-controls">
                {['prepared', 'paused', 'intervention'].includes(run.state) && (
                  <button disabled={!canAct} onClick={() => control.mutate('authorize')}>
                    Authorize under current policy
                  </button>
                )}
                {run.state === 'prepared' && (
                  <button
                    className="primary"
                    disabled={!canAct || run.blockers.length > 0}
                    onClick={() => control.mutate('start')}
                  >
                    {run.manifest.trigger === 'scheduled'
                      ? `Schedule ${phaseLabel}`
                      : `Start ${phaseLabel}`}
                  </button>
                )}
                {['queued', 'scheduled', 'running', 'waiting'].includes(run.state) && (
                  <button disabled={!canAct} onClick={() => control.mutate('pause')}>
                    Pause future actions
                  </button>
                )}
                {['paused', 'intervention'].includes(run.state) && (
                  <>
                    <button
                      disabled={!canAct || run.blockers.length > 0}
                      onClick={() => control.mutate('resume')}
                    >
                      Resume after reauthorization
                    </button>
                    <button
                      disabled={!canAct}
                      onClick={() => {
                        if (
                          window.confirm(
                            'Reconcile failed or unknown operations using their identical original payloads and keys? Only explicitly safe target replay is allowed.',
                          )
                        )
                          control.mutate('reconcile')
                      }}
                    >
                      Reconcile safe operations
                    </button>
                  </>
                )}
                {(active || run.state === 'prepared') && (
                  <button
                    className="danger"
                    disabled={!canSafetyAct}
                    onClick={() => {
                      if (
                        window.confirm(
                          'Stop future dispatch? Accepted or in-flight effects cannot be cancelled and may remain uncertain.',
                        )
                      )
                        control.mutate('stop')
                    }}
                  >
                    Stop future dispatch
                  </button>
                )}
              </div>
            )}
            <p className="preparation-note">
              Pause and stop do not undo accepted effects. Unknown outcomes require reconciliation,
              not a blind retry.
            </p>
          </section>
          {run.approval_required && run.can_review && (
            <section className="glass preparation-panel">
              <h2>Independent execution review</h2>
              <p>
                Review the pinned inputs, target policies, and readiness. Your decision does not
                grant target permissions.
              </p>
              <button disabled={!run.context_id || busy} onClick={pinReview}>
                {reviewBase ? 'Use current review context' : 'Select current review context'}
              </button>
              {reviewChanged && (
                <p className="notice">
                  The run changed after you selected it. Review the latest context explicitly before
                  deciding.
                </p>
              )}
              <form
                className="stack"
                onSubmit={(event) => {
                  event.preventDefault()
                  review.mutate()
                }}
              >
                <label>
                  Execution decision
                  <select
                    value={decision}
                    required
                    disabled={busy}
                    onChange={(event) => {
                      if (
                        event.target.value === 'approved' ||
                        event.target.value === 'rejected' ||
                        event.target.value === ''
                      )
                        setDecision(event.target.value)
                    }}
                  >
                    <option value="">Choose decision</option>
                    <option value="approved">Approve exact execution</option>
                    <option value="rejected">Reject execution</option>
                  </select>
                </label>
                <label>
                  Approval expiry (local time)
                  <input
                    required
                    type="datetime-local"
                    value={expires}
                    disabled={busy}
                    onChange={(event) => setExpires(event.target.value)}
                  />
                </label>
                <button
                  disabled={
                    busy || !reviewBase || reviewChanged || !decision || !expires || !note.trim()
                  }
                >
                  Record execution decision
                </button>
              </form>
            </section>
          )}
          <section className="glass preparation-panel">
            <h2>Recovery preview</h2>
            <p>
              Only registered recovery operations with recorded ownership and version preconditions
              can run automatically. Other writes require an external operator and an auditable
              report. Human changes and evidence are preserved.
            </p>
            <details>
              <summary>{run.manifest.recovery.length} pinned recovery bindings</summary>
              <pre className="run-evidence">{JSON.stringify(run.manifest.recovery, null, 2)}</pre>
            </details>
            {run.can_operate &&
              run.phase === 'exercise' &&
              ['completed', 'stopped', 'failed'].includes(run.state) && (
                <button disabled={!canAct} onClick={() => control.mutate('recover')}>
                  Prepare recovery for review
                </button>
              )}
            <p>
              Recovery is separately authorized under current policy. Preparing it does not dispatch
              effects. Email cannot be undone.
            </p>
          </section>
          {run.can_stop && manualItems.length > 0 && (
            <section className="glass preparation-panel">
              <h2>External outcome report</h2>
              <p>
                Record what an external operator accounted for. This is a human report, not
                automatic proof of a successful effect or reset.
              </p>
              <form
                className="stack"
                onSubmit={(event) => {
                  event.preventDefault()
                  report.mutate()
                }}
              >
                <label>
                  Unresolved item
                  <select
                    required
                    disabled={busy}
                    value={manualStep}
                    onChange={(event) => setManualStep(event.target.value)}
                  >
                    <option value="">Choose item</option>
                    {manualItems.map((step) => (
                      <option
                        key={`${step.phase}/${step.step_id}`}
                        value={`${step.phase}/${step.step_id}`}
                      >
                        {step.phase}: {step.step_id}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  External evidence reference
                  <input
                    required
                    disabled={busy}
                    maxLength={512}
                    value={evidence}
                    onChange={(event) => setEvidence(event.target.value)}
                  />
                </label>
                <button disabled={!canSafetyAct || !manualStep || !evidence.trim()}>
                  Record external report
                </button>
              </form>
            </section>
          )}
        </aside>
      </div>
      <UnsavedChanges
        dirty={!!note || !!decision || !!expires || !!evidence || busy}
        onExport={exportInput}
      />
    </main>
  )
}
