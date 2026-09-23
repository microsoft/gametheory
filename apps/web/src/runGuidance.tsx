import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { components } from './api.generated'

type Schemas = components['schemas']
export type RunBlocker = Schemas['RunBlocker']
export type Readiness = Schemas['ReadinessView']
export type TargetAuthority = Schemas['TargetAuthorityView']

export function RemedyHint({
  blocker,
  admin,
  onOpenPreparation,
  authorizeHere = false,
}: {
  blocker: RunBlocker
  admin: boolean
  onOpenPreparation?: () => void
  authorizeHere?: boolean
}) {
  const settings = (section: string) =>
    admin ? (
      <>
        Open <Link to="/settings">Settings → {section}</Link> to change it.
      </>
    ) : (
      <>Ask an organization administrator to update Settings → {section}.</>
    )
  switch (blocker.remedy) {
    case 'workspace_access':
      return <>Ask a workspace owner to restore the operator’s workspace membership.</>
    case 'run_access':
      return settings('Run access')
    case 'environment_policy':
      return settings('Environments')
    case 'runtime':
      return (
        <>
          The exercise runtime is turned off in this deployment. Ask the deployment operator to
          enable it; this is not a workspace setting.
        </>
      )
    case 'target_bindings':
      return (
        <>
          Ask the deployment operator to add or correct this target in the reviewed bindings file.
          Game Theory never creates target access on its own.
        </>
      )
    case 'readiness':
      return (
        <>
          Ask the deployment operator to verify the target and record a readiness receipt with{' '}
          <code>gametheory execution-readiness</code> that lasts until the window ends.
        </>
      )
    case 'preparation':
    case 'assets':
      return onOpenPreparation ? (
        <>
          Update the preparation, save, and freeze a new preview.{' '}
          <button type="button" className="link-button" onClick={onOpenPreparation}>
            Open the Preparation tab
          </button>
        </>
      ) : (
        <>Update the board’s preparation, freeze a new preview, and create a new run.</>
      )
    case 'authorize':
      return authorizeHere ? (
        <>
          Use <strong>Authorize under current policy</strong> in Operator controls.
        </>
      ) : (
        <>Authorize the run under current policy from its run page.</>
      )
    case 'approval':
      return (
        <>
          An independent reviewer with an explicit reviewer grant must approve this exact run. The
          operator and board contributors cannot approve it.
        </>
      )
    case 'run_state':
      return <>Review the run’s timeline and operator controls.</>
  }
}

export function BlockerChecklist({
  blockers,
  admin,
  onOpenPreparation,
  authorizeHere,
}: {
  blockers: RunBlocker[]
  admin: boolean
  onOpenPreparation?: () => void
  authorizeHere?: boolean
}) {
  if (!blockers.length) return null
  return (
    <ul className="checklist" aria-label="Current blockers">
      {blockers.map((blocker) => (
        <li key={`${blocker.code}/${blocker.message}`} className="attention">
          <AlertTriangle size={16} aria-hidden="true" />
          <div>
            <strong>{blocker.message}</strong>
            <p>
              <RemedyHint
                blocker={blocker}
                admin={admin}
                onOpenPreparation={onOpenPreparation}
                authorizeHere={authorizeHere}
              />
            </p>
          </div>
        </li>
      ))}
    </ul>
  )
}

export function ReadyItem({ children }: { children: React.ReactNode }) {
  return (
    <li className="ready">
      <CheckCircle2 size={16} aria-hidden="true" />
      <div>{children}</div>
    </li>
  )
}

export function AttentionItem({ children }: { children: React.ReactNode }) {
  return (
    <li className="attention">
      <AlertTriangle size={16} aria-hidden="true" />
      <div>{children}</div>
    </li>
  )
}

export function localTime(value: string | null | undefined) {
  if (!value) return 'Unresolved'
  const time = new Date(value)
  return Number.isFinite(time.getTime()) ? time.toLocaleString() : value
}

export function readinessCoverage(
  readiness: Readiness | null | undefined,
  windowEnd?: string | null,
) {
  if (!readiness)
    return { covers: false, text: 'No readiness receipt is recorded for this target.' }
  const expires = Date.parse(readiness.expires_at)
  const ends = windowEnd ? Date.parse(windowEnd) : NaN
  const covers = Number.isFinite(ends) && expires >= ends
  return {
    covers,
    text: covers
      ? `Readiness receipt checked ${localTime(readiness.checked_at)} covers the whole window; it expires ${localTime(readiness.expires_at)}.`
      : `The latest readiness receipt expires ${localTime(readiness.expires_at)}, before the window ends ${localTime(windowEnd)}.`,
  }
}

export function TargetIdentity({ authority }: { authority: TargetAuthority }) {
  return (
    <dl className="preparation-meta">
      <dt>Identity</dt>
      <dd>
        <code>{authority.identity_ref}</code> · client <code>{authority.client_id}</code>
      </dd>
      <dt>Resource</dt>
      <dd>
        <code>{authority.resource_id}</code>
      </dd>
      <dt>Endpoint</dt>
      <dd>
        <code>{authority.endpoint}</code>
        {authority.database && (
          <>
            {' '}
            · database <code>{authority.database}</code>
          </>
        )}
      </dd>
      {authority.token_scope && (
        <>
          <dt>Token audience</dt>
          <dd>
            <code>{authority.token_scope}</code>
          </dd>
        </>
      )}
      <dt>Approved operations</dt>
      <dd>
        {authority.operation_digests.length} exact operation{' '}
        {authority.operation_digests.length === 1 ? 'version' : 'versions'};{' '}
        {authority.replayable_operations.length
          ? `${authority.replayable_operations.length} verified for safe replay`
          : 'none verified for safe replay'}
      </dd>
    </dl>
  )
}
