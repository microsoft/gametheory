import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { LockKeyhole, Settings } from 'lucide-react'
import type { components } from './api.generated'
import { ApiError, ErrorNotice, useSession } from './api'
import { ConflictNotice, exportJson, UnsavedChanges } from './PreparationShared'
import type { Me } from './preparation'
import type { Workspace } from './types'

type Schemas = components['schemas']
type Policy = Schemas['EnvironmentPolicyView']
type PolicyInput = Required<Schemas['EnvironmentPolicyInput']>
type Grant = Schemas['ExecutionGrantView']

function draftOf(policy: Policy): PolicyInput {
  return {
    classification: policy.classification,
    execution_enabled: policy.execution_enabled ?? false,
    approval_required: policy.approval_required ?? false,
  }
}

export function AdminSettings() {
  const { api } = useSession()
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get<Me>('/me') })
  if (me.isPending || (me.error && !me.data))
    return (
      <main className="page">
        <h1>Settings</h1>
        <ErrorNotice error={me.error} />
        {me.isPending && <p role="status">Checking administrator access...</p>}
      </main>
    )
  if (!me.data?.organization_admin)
    return (
      <main className="page">
        <h1>Settings</h1>
        <p className="notice">
          Organization administrator access is required to manage environment policies and run
          access.
        </p>
        <Link to="/">Back to workspaces</Link>
      </main>
    )
  return <AdminSettingsContent permissionError={me.error} />
}

export function AdminSettingsContent({ permissionError }: { permissionError?: unknown }) {
  const { api } = useSession()
  const cache = useQueryClient()
  const [section, setSection] = useState<'environments' | 'access' | 'runtime'>('environments')
  const [selected, setSelected] = useState('')
  const [name, setName] = useState('')
  const [workspace, setWorkspace] = useState('')
  const [dirty, setDirty] = useState(false)
  const [localInput, setLocalInput] = useState<unknown>()
  function leaveInput() {
    return (
      !dirty ||
      window.confirm('Discard unsaved settings input? Save or export it before switching.')
    )
  }
  function selectSection(value: typeof section) {
    if (value !== section && leaveInput()) {
      setDirty(false)
      setSection(value)
    }
  }
  const policies = useQuery({
    queryKey: ['environment-policies'],
    enabled: !permissionError,
    queryFn: () => api.get<Policy[]>('/admin/environment-policies'),
  })
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.get<Workspace[]>('/workspaces'),
  })
  const runtime = useQuery({
    queryKey: ['admin-runtime'],
    queryFn: () => api.get<Schemas['RuntimeStatus']>('/admin/runtime'),
  })
  const create = useMutation({
    mutationFn: () => {
      if (permissionError)
        throw new Error('Administrator access must be confirmed before creating an environment.')
      return api.send<{ id: string }>('/environments', 'POST', { name: name.trim() })
    },
    onSuccess: async (value) => {
      setName('')
      setSelected(value.id)
      await Promise.all([
        cache.invalidateQueries({ queryKey: ['environment-policies'] }),
        cache.invalidateQueries({ queryKey: ['environments'] }),
      ])
    },
  })
  const policy = policies.data?.find((item) => item.environment_id === selected)
  return (
    <main className="page settings-page">
      <Link className="breadcrumb" to="/">
        Workspaces
      </Link>
      <ErrorNotice error={permissionError} />
      <div className="page-heading">
        <div>
          <h1>
            <Settings size={26} aria-hidden="true" /> Settings
          </h1>
          <p>Set the rules for exercise execution across your organization.</p>
        </div>
        <span className="pill">Organization administrator</span>
      </div>
      <nav className="tabs" aria-label="Settings sections">
        <button
          aria-current={section === 'environments'}
          onClick={() => selectSection('environments')}
        >
          Environments
        </button>
        <button aria-current={section === 'access'} onClick={() => selectSection('access')}>
          Run access
        </button>
        <button aria-current={section === 'runtime'} onClick={() => selectSection('runtime')}>
          Runtime status
        </button>
      </nav>
      {section === 'environments' && (
        <>
          <p className="notice">
            <LockKeyhole size={16} aria-hidden="true" /> Production always requires independent
            execution approval. You choose whether other environments require it. A run spanning
            environments follows the strictest rule.
          </p>
          <ErrorNotice error={policies.error ?? create.error} />
          {policies.isPending && <p role="status">Loading environment policies...</p>}
          {policies.error && (
            <button onClick={() => void policies.refetch()}>Retry policies</button>
          )}
          <div className="settings-layout">
            <aside className="settings-environments" aria-label="Environments">
              {!policies.error &&
                policies.data?.map((item) => (
                  <button
                    key={item.environment_id}
                    aria-pressed={selected === item.environment_id}
                    onClick={() => {
                      if (selected !== item.environment_id && !leaveInput()) return
                      setDirty(false)
                      setSelected(item.environment_id)
                    }}
                  >
                    <strong>{item.name}</strong>
                    <span>
                      {item.classification === 'unknown'
                        ? 'Classification needed'
                        : item.classification === 'production'
                          ? 'Production · approval required'
                          : item.approval_required
                            ? 'Approval required'
                            : 'Approval not required'}
                    </span>
                  </button>
                ))}
              {policies.data?.length === 0 && (
                <p>No environments yet. Add one below, then configure its policy.</p>
              )}
              <form
                className="stack"
                onSubmit={(event) => {
                  event.preventDefault()
                  if (leaveInput()) create.mutate()
                }}
              >
                <label>
                  New environment name
                  <input
                    required
                    maxLength={160}
                    value={name}
                    disabled={create.isPending}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
                <button disabled={create.isPending || !name.trim() || !!permissionError}>
                  {create.isPending ? 'Adding...' : 'Add environment'}
                </button>
              </form>
            </aside>
            {policy ? (
              <PolicyEditor
                key={policy.environment_id}
                policy={policy}
                onDirtyChange={setDirty}
                onInputChange={setLocalInput}
                readOnly={!!policies.error || !!permissionError}
              />
            ) : (
              <section className="glass preparation-panel">
                <h2>Select an environment</h2>
                <p>
                  Review its classification and approval policy before allowing configured
                  execution. Existing unclassified environments remain available for authoring.
                </p>
              </section>
            )}
          </div>
        </>
      )}
      {section === 'access' && (
        <section className="stack">
          <h2>Run access</h2>
          <p>
            Membership, preparation review, execution operation, and execution review are separate
            capabilities. Administrator status does not automatically grant run access.
          </p>
          <ErrorNotice error={workspaces.error} />
          <label>
            Workspace
            <select
              value={workspace}
              onChange={(event) => {
                if (leaveInput()) {
                  setDirty(false)
                  setWorkspace(event.target.value)
                }
              }}
            >
              <option value="">Select a workspace</option>
              {workspaces.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          {workspace && (
            <RunAccess
              key={workspace}
              wid={workspace}
              onDirtyChange={setDirty}
              onInputChange={setLocalInput}
              readOnly={!!permissionError}
            />
          )}
        </section>
      )}
      {section === 'runtime' && (
        <section className="glass preparation-panel">
          <h2>Runtime status</h2>
          <ErrorNotice error={runtime.error} />
          {runtime.isPending && <p role="status">Loading configured capabilities...</p>}
          {runtime.data && !runtime.error && (
            <>
              <dl className="preparation-meta">
                <dt>Exercise executor</dt>
                <dd>
                  {runtime.data.execution_enabled
                    ? 'Enabled in deployment configuration'
                    : 'Disabled'}
                </dd>
                <dt>Application SQL</dt>
                <dd>{runtime.data.sql_configured ? 'Configured' : 'Missing configuration'}</dd>
                <dt>Exercise Scheduler</dt>
                <dd>
                  {runtime.data.scheduler_configured ? 'Configured' : 'Missing configuration'}
                </dd>
                <dt>Operator target bindings</dt>
                <dd>
                  {runtime.data.target_bindings_configured ? 'Configured' : 'Missing configuration'}
                </dd>
                <dt>Run-check assistant</dt>
                <dd>
                  {runtime.data.run_assistant_enabled
                    ? 'Enabled: operators can ask for suggested run checks'
                    : 'Disabled (default)'}
                </dd>
              </dl>
              <p className="notice">{runtime.data.message}</p>
              <p>
                Only a deployment operator can configure the executor, target identities, and
                readiness receipts. Settings never provisions resources, changes cloud permissions,
                or sends messages. The run-check assistant only suggests checks for operators to
                review; it never creates, authorizes, or starts runs.
              </p>
            </>
          )}
        </section>
      )}
      <UnsavedChanges
        dirty={dirty || !!name || create.isPending}
        onExport={() =>
          exportJson(
            { new_environment_name: name, local_input: localInput },
            'settings-local-input.json',
          )
        }
      />
    </main>
  )
}

export function PolicyEditor({
  policy,
  onDirtyChange,
  onInputChange,
  readOnly = false,
}: {
  policy: Policy
  onDirtyChange?: (dirty: boolean) => void
  onInputChange?: (input: unknown) => void
  readOnly?: boolean
}) {
  const { api } = useSession()
  const cache = useQueryClient()
  const [base, setBase] = useState(policy)
  const [draft, setDraft] = useState(() => draftOf(policy))
  const [notice, setNotice] = useState('')
  const [showHistory, setShowHistory] = useState(false)
  const path = `/admin/environment-policies/${policy.environment_id}`
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftOf(base))
  const history = useQuery({
    queryKey: ['environment-policy-history', policy.environment_id],
    enabled: showHistory,
    queryFn: () => api.get<Policy[]>(`${path}/history`),
  })
  const save = useMutation({
    mutationFn: () => {
      if (readOnly)
        throw new Error('Reload administrator access and the current policy before saving.')
      return api.send<Policy>(path, 'PUT', draft, base.version)
    },
    onSuccess: async (value) => {
      setBase(value)
      setDraft(draftOf(value))
      setNotice(
        'Policy saved. Queued and running exercises must use the current policy before further actions.',
      )
      await Promise.all([
        cache.invalidateQueries({ queryKey: ['environment-policies'] }),
        cache.invalidateQueries({
          queryKey: ['environment-policy-history', policy.environment_id],
        }),
        cache.invalidateQueries({ queryKey: ['run'] }),
      ])
    },
  })
  const reload = useMutation({
    mutationFn: () => api.read<Policy>(path),
    onSuccess: ({ data, version }) => {
      if (data.version !== version)
        throw new Error('The service returned conflicting policy versions.')
      setBase(data)
      setDraft(draftOf(data))
      save.reset()
      setNotice('Loaded the latest policy.')
    },
  })
  const conflict =
    policy.version > base.version ? new ApiError(409, 'A newer policy exists.', null) : save.error
  const busy = save.isPending || reload.isPending
  useEffect(() => {
    onDirtyChange?.(dirty || busy)
    return () => onDirtyChange?.(false)
  }, [dirty, busy, onDirtyChange])
  useEffect(() => {
    onInputChange?.({ environment_id: policy.environment_id, base_version: base.version, ...draft })
  }, [draft, base.version, policy.environment_id, onInputChange])
  const exportInput = () =>
    exportJson(
      { environment_id: policy.environment_id, base_version: base.version, ...draft },
      'environment-policy-input.json',
    )
  return (
    <section className="glass preparation-panel">
      <div className="page-heading">
        <h2>{policy.name}</h2>
        <span className="pill">Policy version {base.version}</span>
      </div>
      <ErrorNotice error={save.error ?? reload.error} />
      <ConflictNotice
        error={conflict}
        onExport={exportInput}
        onReload={() => reload.mutate()}
        pending={busy}
      />
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <fieldset className="preparation-inputs" disabled={busy || readOnly}>
          <legend>Execution policy</legend>
          <label>
            Environment classification
            <select
              value={draft.classification}
              disabled={base.classification === 'production'}
              onChange={(event) => {
                const value = event.target.value
                if (value === 'production' || value === 'nonproduction' || value === 'unknown')
                  setDraft({
                    ...draft,
                    classification: value,
                    approval_required:
                      value === 'production' ||
                      (value !== 'nonproduction' && draft.approval_required),
                    execution_enabled: value !== 'unknown' && draft.execution_enabled,
                  })
              }}
            >
              <option value="unknown">Unclassified</option>
              <option value="nonproduction">Nonproduction</option>
              <option value="production">Production</option>
            </select>
          </label>
          <label className="settings-check">
            <input
              type="checkbox"
              checked={draft.execution_enabled}
              disabled={draft.classification === 'unknown'}
              onChange={(event) => setDraft({ ...draft, execution_enabled: event.target.checked })}
            />
            <span>Allow configured execution in this environment</span>
          </label>
          <p>
            Target bindings, live readiness, operator access, and an execution window are still
            required.
          </p>
          <label className="settings-check">
            <input
              type="checkbox"
              checked={draft.approval_required}
              disabled={draft.classification === 'production' || draft.classification === 'unknown'}
              onChange={(event) => setDraft({ ...draft, approval_required: event.target.checked })}
            />
            <span>Require independent execution approval</span>
          </label>
          {draft.classification === 'production' ? (
            <p className="notice">
              Production approval is mandatory and cannot be disabled. Production environments
              cannot be downgraded to bypass this rule.
            </p>
          ) : (
            <p>
              Without this requirement, an explicitly granted operator may start a ready run.
              Preparation approval is never reused as execution approval.
            </p>
          )}
        </fieldset>
        <div className="toolbar">
          <button
            className="primary"
            disabled={!dirty || busy || readOnly || policy.version > base.version}
          >
            {save.isPending ? 'Saving...' : 'Save environment policy'}
          </button>
          <button type="button" onClick={exportInput}>
            Export local input
          </button>
        </div>
      </form>
      <button
        className="settings-history-button"
        type="button"
        aria-expanded={showHistory}
        onClick={() => setShowHistory(!showHistory)}
      >
        Policy history
      </button>
      {showHistory && (
        <>
          <ErrorNotice error={history.error} />
          <ol className="run-timeline">
            {!history.error &&
              history.data?.map((item) => (
                <li key={item.version}>
                  <strong>Version {item.version}</strong>
                  <span>
                    {item.classification} ·{' '}
                    {item.execution_enabled ? 'Execution permitted' : 'Execution disabled'} ·{' '}
                    {item.approval_required ? 'Approval required' : 'Approval not required'}
                  </span>
                  <small>
                    {new Date(item.updated_at).toLocaleString()} · {item.updated_by}
                  </small>
                </li>
              ))}
          </ol>
        </>
      )}
      {!onDirtyChange && <UnsavedChanges dirty={dirty || busy} onExport={exportInput} />}
    </section>
  )
}

export function RunAccess({
  wid,
  onDirtyChange,
  onInputChange,
  readOnly = false,
}: {
  wid: string
  onDirtyChange?: (dirty: boolean) => void
  onInputChange?: (input: unknown) => void
  readOnly?: boolean
}) {
  const { api } = useSession()
  const cache = useQueryClient()
  const [objectId, setObjectId] = useState('')
  const [capability, setCapability] = useState<'operator' | 'reviewer'>('operator')
  const path = `/workspaces/${wid}/execution-grants`
  const key = [wid, 'execution-grants']
  const grants = useQuery({ queryKey: key, queryFn: () => api.get<Grant[]>(path) })
  const change = useMutation({
    mutationFn: async (remove?: Grant) => {
      if (readOnly) throw new Error('Confirm administrator access before changing run grants.')
      if (remove) await api.remove(`${path}/${remove.object_id}/${remove.capability}`)
      else await api.send<Grant>(path, 'PUT', { object_id: objectId.trim(), capability })
    },
    onSuccess: async () => {
      setObjectId('')
      await Promise.all([
        cache.invalidateQueries({ queryKey: key }),
        cache.invalidateQueries({ queryKey: ['run'] }),
      ])
    },
  })
  useEffect(() => {
    onDirtyChange?.(!!objectId || change.isPending)
    return () => onDirtyChange?.(false)
  }, [objectId, change.isPending, onDirtyChange])
  useEffect(() => {
    onInputChange?.({ workspace_id: wid, object_id: objectId, capability })
  }, [wid, objectId, capability, onInputChange])
  return (
    <section className="glass preparation-panel">
      <h3>Explicit execution capabilities</h3>
      <p>
        Users need existing workspace membership. Required reviewers cannot prepare or operate the
        run they approve. Revocation blocks future actions and invalidates affected approvals.
      </p>
      <Link to={`/w/${wid}?section=access`}>
        Manage workspace membership and preparation reviewers
      </Link>
      <ErrorNotice error={grants.error ?? change.error} />
      {grants.isPending && <p role="status">Loading execution grants...</p>}
      <form
        className="preparation-fields"
        onSubmit={(event) => {
          event.preventDefault()
          change.mutate(undefined)
        }}
      >
        <label>
          Entra object ID
          <input
            required
            value={objectId}
            disabled={change.isPending}
            onChange={(event) => setObjectId(event.target.value)}
            pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
          />
        </label>
        <label>
          Execution capability
          <select
            value={capability}
            disabled={change.isPending}
            onChange={(event) => {
              if (event.target.value === 'operator' || event.target.value === 'reviewer')
                setCapability(event.target.value)
            }}
          >
            <option value="operator">Run operator</option>
            <option value="reviewer">Independent execution reviewer</option>
          </select>
        </label>
        <button
          disabled={
            readOnly || change.isPending || !objectId.trim() || !!grants.error || !grants.data
          }
        >
          Grant execution capability
        </button>
      </form>
      {!grants.error && grants.data?.length === 0 && (
        <p>No execution capabilities have been granted.</p>
      )}
      <ul className="preparation-list">
        {!grants.error &&
          grants.data?.map((item) => (
            <li key={item.id}>
              <strong>
                {item.capability === 'operator' ? 'Run operator' : 'Execution reviewer'}
              </strong>
              <code>{item.object_id}</code>
              <button
                disabled={change.isPending || readOnly}
                onClick={() => {
                  if (
                    window.confirm(
                      'Revoke this execution capability? Future actions and existing approvals may become unavailable.',
                    )
                  )
                    change.mutate(item)
                }}
              >
                Revoke
              </button>
            </li>
          ))}
      </ul>
      {!onDirtyChange && (
        <UnsavedChanges
          dirty={!!objectId || change.isPending}
          onExport={() =>
            exportJson({ object_id: objectId, capability }, 'execution-grant-input.json')
          }
        />
      )}
    </section>
  )
}
