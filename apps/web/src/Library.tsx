import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowRight, FolderOpen, Network, Plus, ShieldCheck } from 'lucide-react'
import { ErrorNotice, useSession } from './api'
import type { Connection, Environment, Member, Scenario, Workspace } from './types'
import type { Me } from './preparation'
import { Approvers } from './Approvers'
import { Boards } from './Boards'

export function Library() {
  const { api } = useSession()
  const cache = useQueryClient()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.get<Workspace[]>('/workspaces'),
  })
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<Me>('/me'),
  })
  const create = useMutation({
    mutationFn: () => api.send<Workspace>('/workspaces', 'POST', { name }),
    onSuccess: (workspace) => {
      void cache.invalidateQueries({ queryKey: ['workspaces'] })
      navigate(`/w/${workspace.id}`)
    },
  })
  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">YOUR CONTROL PLANE</span>
          <h1>Workspaces</h1>
          <p>
            A shared place to plan, refine, and prepare. Participants stay in the tools they already
            use.
          </p>
        </div>
        <span className="badge">
          <ShieldCheck size={16} /> Authoring only
        </span>
      </div>
      <ErrorNotice error={workspaces.error ?? me.error ?? create.error} />
      {me.data?.organization_admin && (
        <form
          className="inline-form glass"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate()
          }}
        >
          <label className="grow">
            New workspace
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={160}
              placeholder="e.g. Regional resilience"
            />
          </label>
          <button className="primary" disabled={create.isPending}>
            <Plus size={16} />
            Create workspace
          </button>
        </form>
      )}
      {workspaces.isPending && <p role="status">Loading workspaces...</p>}
      <div className="card-grid">
        {workspaces.data?.map((workspace) => (
          <Link className="glass workspace-card" key={workspace.id} to={`/w/${workspace.id}`}>
            <FolderOpen size={24} />
            <span className="pill">{workspace.role}</span>
            <h2>{workspace.name}</h2>
            <p>Scenarios, shared assets, and planning context.</p>
            <span className="text-action">
              Open workspace <ArrowRight size={16} />
            </span>
          </Link>
        ))}
      </div>
      {workspaces.data?.length === 0 && (
        <div className="empty glass">
          <FolderOpen size={32} />
          <h2>No workspaces yet</h2>
          <p>
            {me.data?.organization_admin
              ? 'Create your first workspace above.'
              : 'Ask your organization administrator to grant workspace access.'}
          </p>
        </div>
      )}
    </main>
  )
}

export function WorkspacePage() {
  const { wid = '' } = useParams()
  const { api } = useSession()
  const cache = useQueryClient()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [search, setSearch] = useSearchParams()
  const selectedSection = search.get('section') ?? ''
  const tab = ['scenarios', 'boards', 'connections', 'access'].includes(selectedSection)
    ? selectedSection
    : 'scenarios'
  const setTab = (section: string) => setSearch({ section })
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.get<Workspace[]>('/workspaces'),
  })
  const workspace = workspaces.data?.find((w) => w.id === wid)
  const scenarios = useQuery({
    queryKey: [wid, 'scenarios'],
    queryFn: () => api.get<Scenario[]>(`/workspaces/${wid}/scenarios`),
  })
  const create = useMutation({
    mutationFn: () => api.send<Scenario>(`/workspaces/${wid}/scenarios`, 'POST', { name }),
    onSuccess: (scenario) => {
      void cache.invalidateQueries({ queryKey: [wid, 'scenarios'] })
      navigate(`/w/${wid}/s/${scenario.id}`)
    },
  })
  const editor = workspace && workspace.role !== 'viewer'
  return (
    <main className="page">
      <Link className="breadcrumb" to="/">
        Workspaces
      </Link>
      <div className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE</span>
          <h1>{workspace?.name ?? 'Workspace'}</h1>
          <p>Turn intent into a clear, reviewable scenario.</p>
        </div>
        <span className="pill">{workspace?.role}</span>
      </div>
      <nav className="tabs" aria-label="Workspace sections">
        <button aria-current={tab === 'scenarios'} onClick={() => setTab('scenarios')}>
          Scenarios
        </button>
        <button aria-current={tab === 'boards'} onClick={() => setTab('boards')}>
          Game boards
        </button>
        <button aria-current={tab === 'connections'} onClick={() => setTab('connections')}>
          Connection inventory
        </button>
        <button aria-current={tab === 'access'} onClick={() => setTab('access')}>
          Access
        </button>
      </nav>
      <ErrorNotice error={scenarios.error ?? workspaces.error ?? create.error} />
      {tab === 'scenarios' && (
        <>
          {editor && (
            <form
              className="inline-form glass"
              onSubmit={(e) => {
                e.preventDefault()
                create.mutate()
              }}
            >
              <label className="grow">
                New scenario
                <input
                  required
                  maxLength={160}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="What do you want to prepare for?"
                />
              </label>
              <button className="primary" disabled={create.isPending}>
                <Plus size={16} />
                Create scenario
              </button>
            </form>
          )}
          <div className="card-grid">
            {scenarios.data?.map((scenario) => (
              <Link
                key={scenario.id}
                className="glass workspace-card"
                to={`/w/${wid}/s/${scenario.id}`}
              >
                <span className="eyebrow">DRAFT {scenario.version}</span>
                <h2>{scenario.content.title}</h2>
                <p>
                  {scenario.content.objectives.length} objectives · {scenario.content.nodes.length}{' '}
                  flow steps
                </p>
                <span className="text-action">
                  Open studio <ArrowRight size={16} />
                </span>
              </Link>
            ))}
          </div>
          {scenarios.isPending && <p role="status">Loading scenarios...</p>}
          {scenarios.data?.length === 0 && (
            <div className="empty glass">
              <h2>Start with an objective.</h2>
              <p>
                Create a scenario, shape the plan, and ask the planning assistant to help refine it.
              </p>
            </div>
          )}
        </>
      )}
      {tab === 'connections' && <Connections wid={wid} editable={!!editor} />}
      {tab === 'boards' && <Boards wid={wid} editable={!!editor} />}
      {tab === 'access' && (
        <div className="stack">
          {workspace?.role === 'owner' && <Access wid={wid} />}
          <Approvers wid={wid} />
        </div>
      )}
    </main>
  )
}

function Connections({ wid, editable }: { wid: string; editable: boolean }) {
  const { api } = useSession()
  const cache = useQueryClient()
  const [name, setName] = useState('')
  const [kind, setKind] = useState('sql')
  const [environment, setEnvironment] = useState('')
  const [scope, setScope] = useState('workspace')
  const [assignments, setAssignments] = useState<string[]>([])
  const [description, setDescription] = useState('')
  const [environmentName, setEnvironmentName] = useState('')
  const rows = useQuery({
    queryKey: [wid, 'connections'],
    queryFn: () => api.get<Connection[]>(`/workspaces/${wid}/connections`),
  })
  const environments = useQuery({
    queryKey: ['environments'],
    queryFn: () => api.get<Environment[]>('/environments'),
  })
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.get<Workspace[]>('/workspaces'),
  })
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<Me>('/me'),
  })
  const create = useMutation({
    mutationFn: () =>
      api.send(`/workspaces/${wid}/connections`, 'POST', {
        name,
        kind,
        environment_id: environment,
        scope,
        description,
        workspace_ids: scope === 'assigned' ? assignments : [],
      }),
    onSuccess: () => {
      setName('')
      setDescription('')
      void cache.invalidateQueries({ queryKey: [wid, 'connections'] })
    },
  })
  const createEnvironment = useMutation({
    mutationFn: () => api.send('/environments', 'POST', { name: environmentName }),
    onSuccess: () => {
      setEnvironmentName('')
      void cache.invalidateQueries({ queryKey: ['environments'] })
    },
  })
  return (
    <section className="stack">
      <div className="notice">
        <strong>Inventory, not activation.</strong> Connections describe intended targets. No
        credentials are collected and no external system is contacted in this milestone.
      </div>
      <ErrorNotice
        error={
          rows.error ?? environments.error ?? create.error ?? me.error ?? createEnvironment.error
        }
      />
      {editable && (
        <form
          className="glass form-grid"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate()
          }}
        >
          <label>
            Name
            <input
              required
              maxLength={160}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            Integration
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="sql">Azure SQL</option>
              <option value="rest">REST / OpenAPI</option>
              <option value="graph">Microsoft Graph</option>
              <option value="mcp">MCP</option>
            </select>
          </label>
          <label>
            Environment
            <select required value={environment} onChange={(e) => setEnvironment(e.target.value)}>
              <option value="">Choose environment</option>
              {environments.data?.map((env) => (
                <option key={env.id} value={env.id}>
                  {env.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Availability
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="workspace">This workspace</option>
              {me.data?.organization_admin && (
                <>
                  <option value="organization">
                    All organization workspaces, including future ones
                  </option>
                  <option value="assigned">Assigned workspaces</option>
                </>
              )}
            </select>
          </label>
          {scope === 'assigned' && (
            <fieldset>
              <legend>Assigned workspaces</legend>
              {workspaces.data?.map((w) => (
                <label className="check" key={w.id}>
                  <input
                    type="checkbox"
                    checked={assignments.includes(w.id)}
                    onChange={(e) =>
                      setAssignments(
                        e.target.checked
                          ? [...assignments, w.id]
                          : assignments.filter((id) => id !== w.id),
                      )
                    }
                  />
                  {w.name}
                </label>
              ))}
            </fieldset>
          )}
          <label className="full">
            Description (no credentials)
            <textarea
              value={description}
              maxLength={2000}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <button className="primary" disabled={create.isPending}>
            Add to inventory
          </button>
        </form>
      )}
      {me.data?.organization_admin && (
        <form
          className="inline-form glass"
          onSubmit={(e) => {
            e.preventDefault()
            createEnvironment.mutate()
          }}
        >
          <label className="grow">
            Custom environment
            <input
              required
              maxLength={160}
              value={environmentName}
              onChange={(e) => setEnvironmentName(e.target.value)}
            />
          </label>
          <button disabled={createEnvironment.isPending}>Add environment</button>
        </form>
      )}
      <div className="card-grid">
        {rows.data?.map((row) => (
          <article className="glass workspace-card" key={row.id}>
            <Network size={20} />
            <h3>{row.name}</h3>
            <p>{row.description}</p>
            <span className="pill">
              {environments.data?.find((e) => e.id === row.environment_id)?.name ??
                'Environment unavailable'}
            </span>{' '}
            <span className="pill">{row.scope}</span>
            <p className="muted">Inventory only · {row.kind}</p>
            <Link className="text-action" to={`/w/${wid}/connections/${row.id}`}>
              {me.data?.organization_admin ? 'Configure & view history' : 'Configuration history'}
              <ArrowRight size={16} />
            </Link>
          </article>
        ))}
      </div>
      {rows.isPending && <p role="status">Loading connection inventory...</p>}
      {rows.data?.length === 0 && (
        <div className="empty glass">
          <h2>No connections registered</h2>
          <p>
            {editable
              ? 'Create an inventory record, then ask an organization administrator to register its configuration and operation catalog.'
              : 'A workspace editor can create inventory. An organization administrator manages operation catalogs and target metadata.'}
          </p>
        </div>
      )}
    </section>
  )
}

function Access({ wid }: { wid: string }) {
  const { api } = useSession()
  const cache = useQueryClient()
  const [objectId, setObjectId] = useState('')
  const [role, setRole] = useState<Member['role']>('viewer')
  const members = useQuery({
    queryKey: [wid, 'members'],
    queryFn: () => api.get<Member[]>(`/workspaces/${wid}/members`),
  })
  const save = useMutation({
    mutationFn: () => api.send(`/workspaces/${wid}/members`, 'PUT', { object_id: objectId, role }),
    onSuccess: () => {
      setObjectId('')
      void cache.invalidateQueries({ queryKey: [wid, 'members'] })
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.remove(`/workspaces/${wid}/members/${id}`),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: [wid, 'members'] })
    },
  })
  return (
    <section className="stack">
      <p>
        Grant access using the person's object ID in this organization's Entra tenant. Organization
        administrators retain owner access.
      </p>
      <ErrorNotice error={members.error ?? save.error ?? remove.error} />
      <form
        className="inline-form glass"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <label className="grow">
          Entra object ID
          <input
            required
            pattern="[0-9a-fA-F-]{36}"
            value={objectId}
            onChange={(e) => setObjectId(e.target.value)}
          />
        </label>
        <label>
          Role
          <select value={role} onChange={(e) => setRole(e.target.value as Member['role'])}>
            <option value="viewer">Viewer</option>
            <option value="editor">Editor</option>
            <option value="owner">Owner</option>
          </select>
        </label>
        <button className="primary" disabled={save.isPending}>
          Set access
        </button>
      </form>
      {members.data?.map((member) => (
        <div className="glass member-row" key={member.object_id}>
          <code>{member.object_id}</code>
          <span className="pill">{member.role}</span>
          <button
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm('Remove this workspace membership?'))
                remove.mutate(member.object_id)
            }}
          >
            Remove
          </button>
        </div>
      ))}
    </section>
  )
}
