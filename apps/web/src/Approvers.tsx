import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { components } from './api.generated'
import { ErrorNotice, useSession } from './api'
import { ConflictNotice, exportJson, UnsavedChanges } from './PreparationShared'
import type { ApproverGrant, Me } from './preparation'

export function Approvers({ wid }: { wid: string }) {
  const { api } = useSession()
  const cache = useQueryClient()
  const path = `/workspaces/${wid}/approvers`
  const key = [wid, 'approvers']
  const [objectId, setObjectId] = useState('')
  const [notice, setNotice] = useState('')
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get<Me>('/me') })
  const grants = useQuery({ queryKey: key, queryFn: () => api.get<ApproverGrant[]>(path) })
  async function refresh() {
    await Promise.all([
      cache.invalidateQueries({ queryKey: key }),
      cache.invalidateQueries({ queryKey: [wid, 'board'] }),
      cache.invalidateQueries({ queryKey: [wid, 'boards'] }),
    ])
  }
  const grant = useMutation({
    mutationFn: () => {
      if (!grants.data || grants.error)
        throw new Error('Load the latest approver grants before changing access.')
      const body: components['schemas']['ApproverGrantInput'] = { object_id: objectId.trim() }
      return api.send<ApproverGrant>(path, 'PUT', body)
    },
    onSuccess: async () => {
      setObjectId('')
      setNotice(
        'Explicit preparation approver capability granted. Existing workspace membership is still required, and contributors cannot self-approve.',
      )
      await refresh()
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => {
      if (!grants.data || grants.error)
        throw new Error('Load the latest approver grants before changing access.')
      return api.remove(`${path}/${id}`)
    },
    onSuccess: async () => {
      setNotice(
        'Approver capability revoked. Decision history is retained; affected approvals no longer retain current validity.',
      )
      await refresh()
    },
  })
  const reload = useMutation({
    mutationFn: () => api.get<ApproverGrant[]>(path),
    onSuccess: (value) => {
      cache.setQueryData(key, value)
      setObjectId('')
      grant.reset()
      revoke.reset()
      setNotice('Loaded the latest approver grants.')
    },
  })
  const busy = grant.isPending || revoke.isPending || reload.isPending
  const error = me.error ?? grants.error ?? grant.error ?? revoke.error ?? reload.error
  const exportInput = () =>
    exportJson(
      { proposed_object_id: objectId, visible_grants: grants.data ?? [] },
      'preparation-approver-input.json',
    )
  return (
    <section className="glass preparation-panel" aria-labelledby="approvers-heading">
      <h2 id="approvers-heading">Preparation approvers</h2>
      <p className="preparation-note">
        Approving preparation is an explicit workspace capability. Workspace owner, editor, and
        organization administrator roles do not automatically grant it. Board creators and all
        preparation contributors are excluded from approving their own work.
      </p>
      <ErrorNotice error={error} />
      <ConflictNotice
        error={grant.error ?? revoke.error}
        onExport={exportInput}
        onReload={() => reload.mutate()}
        pending={busy}
      />
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {(me.isPending || grants.isPending) && (
        <p role="status">Loading explicit approver capabilities...</p>
      )}
      {grants.error && (
        <button type="button" onClick={() => void grants.refetch()}>
          Retry approver access
        </button>
      )}
      {me.data?.organization_admin ? (
        <form
          className="preparation-fields"
          onSubmit={(event) => {
            event.preventDefault()
            grant.mutate()
          }}
        >
          <label>
            Approver Entra object ID
            <input
              required
              value={objectId}
              disabled={busy || !!me.error}
              pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
              onChange={(event) => setObjectId(event.target.value)}
            />
          </label>
          <div className="preparation-actions">
            <button
              className="primary"
              disabled={busy || !objectId.trim() || !grants.data || !!grants.error || !!me.error}
            >
              {grant.isPending ? 'Granting...' : 'Grant preparation approval'}
            </button>
          </div>
          <p className="preparation-note full">
            Granting this capability does not create workspace membership or authorize execution.
            Use membership controls separately when workspace access is needed.
          </p>
        </form>
      ) : (
        me.data && (
          <p className="notice">
            Read-only approver access. Only organization administrators may grant or revoke this
            capability.
          </p>
        )
      )}
      {!grants.error && grants.data?.length === 0 && (
        <p>
          No explicit approvers assigned. An organization administrator must grant a separate,
          non-contributing reviewer access before a preparation can be approved.
        </p>
      )}
      <ul className="preparation-list">
        {!grants.error &&
          grants.data?.map((item) => (
            <li key={item.id}>
              <h3>
                <code>{item.object_id}</code>
              </h3>
              <span className="pill">Explicit preparation approver</span>
              <dl className="preparation-meta">
                <dt>Granted by</dt>
                <dd>
                  <code>{item.granted_by}</code>
                </dd>
                <dt>Granted at</dt>
                <dd>{new Date(item.granted_at).toLocaleString()}</dd>
              </dl>
              {me.data?.organization_admin && (
                <button
                  type="button"
                  disabled={busy || !!grants.error || !!me.error}
                  onClick={() => {
                    if (
                      window.confirm(
                        'Revoke this explicit approver capability? Existing decision history stays, but affected approvals become invalid.',
                      )
                    )
                      revoke.mutate(item.object_id)
                  }}
                >
                  Revoke approver capability
                </button>
              )}
            </li>
          ))}
      </ul>
      <UnsavedChanges dirty={!!objectId || busy} onExport={exportInput} />
    </section>
  )
}
