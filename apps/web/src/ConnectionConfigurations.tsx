import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { ErrorNotice, useSession } from './api'
import { ConfigurationDetails } from './ConfigurationDetails'
import { ConfigurationEditor, type ConfigurationContent } from './ConfigurationEditor'
import { exportJson, isConflict } from './PreparationShared'
import type { Configuration, Me } from './preparation'
import type { Asset, Connection } from './types'

export function ConnectionConfigurations() {
  const { wid = '', cid = '' } = useParams()
  return <ConnectionConfigurationContent key={`${wid}/${cid}`} wid={wid} cid={cid} />
}

function ConnectionConfigurationContent({ wid, cid }: { wid: string; cid: string }) {
  const { api } = useSession()
  const cache = useQueryClient()
  const path = `/workspaces/${wid}/connections/${cid}/configurations`
  const queryKey = [wid, 'configurations', cid]
  const [copied, setCopied] = useState<{ content: ConfigurationContent; key: string }>()
  const [notice, setNotice] = useState('')
  const inventory = useQuery({
    queryKey: [wid, 'connections'],
    queryFn: () => api.get<Connection[]>(`/workspaces/${wid}/connections`),
  })
  const connection = inventory.data?.find((item) => item.id === cid)
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get<Me>('/me') })
  const configurations = useQuery({
    queryKey,
    queryFn: () => api.get<Configuration[]>(path),
    enabled: !!connection && connection.kind !== 'mcp',
  })
  const assets = useQuery({
    queryKey: [wid, 'assets'],
    queryFn: () => api.get<Asset[]>(`/workspaces/${wid}/assets`),
    enabled: connection?.kind === 'graph' && !!me.data?.organization_admin,
  })
  async function refresh() {
    const value = await api.get<Configuration[]>(path)
    cache.setQueryData(queryKey, value)
    await cache.invalidateQueries({ queryKey: [wid, 'board'] })
  }
  const register = useMutation({
    mutationFn: (content: ConfigurationContent) => {
      if (!configurations.data || configurations.error)
        throw new Error('Load the current configuration history before registering.')
      return api.send<Configuration>(path, 'POST', content)
    },
    onSuccess: async (value) => {
      setNotice(`Configuration ${value.version} registered. Live readiness is unverified.`)
      await cache.invalidateQueries({ queryKey })
      await cache.invalidateQueries({ queryKey: [wid, 'board'] })
    },
  })
  const withdraw = useMutation({
    mutationFn: (configuration: Configuration) =>
      api.send<Configuration>(
        `${path}/${configuration.id}/withdraw`,
        'POST',
        undefined,
        configuration.version,
      ),
    onSuccess: async () => {
      setNotice(
        'Configuration withdrawn. Its immutable history is retained, and preparations using it cannot retain current approval.',
      )
      await cache.invalidateQueries({ queryKey })
      await cache.invalidateQueries({ queryKey: [wid, 'board'] })
    },
  })
  const error =
    inventory.error ??
    me.error ??
    configurations.error ??
    withdraw.error ??
    (connection?.kind === 'graph' && me.data?.organization_admin ? assets.error : undefined)
  const busy = register.isPending || withdraw.isPending
  const visibleConfigurations = configurations.error ? undefined : configurations.data
  return (
    <main className="page">
      <Link className="breadcrumb" to={`/w/${wid}?section=connections`}>
        <ArrowLeft size={14} /> Connection inventory
      </Link>
      <div className="page-heading">
        <div>
          <h1>{connection?.name ?? 'Connection configuration'}</h1>
          <p>
            Concrete target descriptions and external operation contracts, preserved as immutable
            revisions.
          </p>
        </div>
        {connection && (
          <span className="pill">
            {connection.kind} · {connection.scope}
          </span>
        )}
      </div>
      <div className="notice">
        <strong>Registration is not activation.</strong> No target is contacted, no credentials are
        collected, and no connection, permission, delivery, or execution claim is made.
      </div>
      <ErrorNotice error={error} />
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {isConflict(withdraw.error) && (
        <div className="notice">
          Configuration history changed. Your registration input is still here.
          <div className="toolbar">
            <button
              type="button"
              onClick={() => exportJson(configurations.data, 'configuration-history.json')}
            >
              Export visible history
            </button>
            <button
              type="button"
              onClick={() => {
                if (
                  window.confirm(
                    'Reload configuration history? Unsaved registration input will be kept.',
                  )
                ) {
                  void configurations.refetch()
                  withdraw.reset()
                }
              }}
            >
              Reload configuration history
            </button>
          </div>
        </div>
      )}
      {(inventory.isPending || me.isPending) && <p role="status">Loading connection access...</p>}
      {inventory.data && !connection && (
        <p className="notice">
          This connection is unavailable in this workspace. Ask a workspace owner to check inventory
          access.
        </p>
      )}
      {connection?.kind === 'mcp' && (
        <p className="notice">
          MCP is preserved as inventory only. Preparation operation bindings are not supported for
          this integration.
        </p>
      )}
      {connection && connection.kind !== 'mcp' && (
        <div className="stack">
          <section className="glass preparation-panel">
            <h2>Configuration history</h2>
            {configurations.isPending && (
              <p role="status">Loading immutable configuration revisions...</p>
            )}
            {configurations.error && (
              <button type="button" onClick={() => void configurations.refetch()}>
                Retry configuration history
              </button>
            )}
            {visibleConfigurations?.length === 0 && (
              <p>
                No configuration revisions registered. Inventory metadata alone cannot bind a
                preparation operation.
              </p>
            )}
            <ul className="preparation-list preparation-history">
              {visibleConfigurations?.map((configuration) => (
                <li key={configuration.id}>
                  <details>
                    <summary>
                      <span>
                        Configuration {configuration.version} · {configuration.content.catalog.name}
                      </span>
                      <span className="pill">
                        {configuration.withdrawn_at ? 'Withdrawn' : 'Registered · live-unverified'}
                      </span>
                    </summary>
                    <dl className="preparation-meta">
                      <dt>Configuration ID</dt>
                      <dd>
                        <code>{configuration.id}</code>
                      </dd>
                      <dt>Environment inventory</dt>
                      <dd>{configuration.environment_name}</dd>
                      <dt>Registered by</dt>
                      <dd>
                        <code>{configuration.created_by}</code>
                      </dd>
                      <dt>Registered at</dt>
                      <dd>{new Date(configuration.created_at).toLocaleString()}</dd>
                      <dt>Immutable digest</dt>
                      <dd>
                        <code>{configuration.digest}</code>
                      </dd>
                      {configuration.withdrawn_at && (
                        <>
                          <dt>Withdrawn at</dt>
                          <dd>{new Date(configuration.withdrawn_at).toLocaleString()}</dd>
                          <dt>Withdrawn by</dt>
                          <dd>
                            <code>{configuration.withdrawn_by}</code>
                          </dd>
                        </>
                      )}
                    </dl>
                    {configuration.template_asset && (
                      <p className="preparation-note">
                        Pinned template: {configuration.template_asset.name} ·{' '}
                        <code>{configuration.template_asset.id}</code> · SHA-256{' '}
                        <code>{configuration.template_asset.sha256}</code>
                      </p>
                    )}
                    <ConfigurationDetails content={configuration.content} />
                    <div className="preparation-actions">
                      <button
                        type="button"
                        onClick={() =>
                          exportJson(configuration, `configuration-${configuration.id}.json`)
                        }
                      >
                        Export immutable configuration
                      </button>
                      {me.data?.organization_admin && (
                        <>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => {
                              if (
                                window.confirm(
                                  'Replace any unsaved registration input with this revision’s metadata? A new registration will not change this immutable revision.',
                                )
                              )
                                setCopied({
                                  content: configuration.content,
                                  key: crypto.randomUUID(),
                                })
                            }}
                          >
                            Use details for a new revision
                          </button>
                          {!configuration.withdrawn_at && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => {
                                if (
                                  window.confirm(
                                    'Withdraw this configuration? Its history stays intact, but affected preparations will lose current approval.',
                                  )
                                )
                                  withdraw.mutate(configuration)
                              }}
                            >
                              Withdraw configuration {configuration.version}
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </details>
                </li>
              ))}
            </ul>
          </section>
          {me.data && !me.data.organization_admin && (
            <p className="notice">
              Read-only configuration history. Only organization administrators may register or
              withdraw target configurations.
            </p>
          )}
          {me.data?.organization_admin && configurations.data && (
            <ConfigurationEditor
              key={copied?.key ?? 'new'}
              kind={connection.kind}
              assets={assets.data ?? []}
              initial={copied?.content}
              disabled={busy || !!configurations.error || !!me.error}
              onRegister={async (content) => {
                await register.mutateAsync(content)
              }}
              onReload={refresh}
            />
          )}
        </div>
      )}
    </main>
  )
}
