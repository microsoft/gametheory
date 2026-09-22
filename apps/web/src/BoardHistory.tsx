import { ConfigurationDetails } from './ConfigurationDetails'
import { exportJson } from './PreparationShared'
import {
  isResultReference,
  preparationReason,
  type Approval,
  type BoardView,
  type Me,
  type Preview,
} from './preparation'

export function BoardProvenance({ board }: { board: BoardView }) {
  return (
    <aside className="glass preparation-panel">
      <h2>Pinned provenance</h2>
      <p className="preparation-note">
        This board starts from a published snapshot. Later scenario edits and asset uploads do not
        replace these inputs.
      </p>
      <dl className="preparation-meta">
        <dt>Scenario</dt>
        <dd>{board.scenario.content.title}</dd>
        <dt>Published revision</dt>
        <dd>{board.revision_version}</dd>
        <dt>Scenario ID</dt>
        <dd>
          <code>{board.scenario_id}</code>
        </dd>
        <dt>Board creator</dt>
        <dd>
          <code>{board.created_by}</code>
        </dd>
        <dt>Board version</dt>
        <dd>{board.version}</dd>
        <dt>Created</dt>
        <dd>{new Date(board.created_at).toLocaleString()}</dd>
      </dl>
      <details>
        <summary>Pinned asset versions ({board.assets?.length ?? 0})</summary>
        <ul className="preparation-list">
          {(board.assets ?? []).map((asset) => (
            <li key={asset.id}>
              <strong>{asset.name}</strong>
              <dl className="preparation-meta">
                <dt>Version ID</dt>
                <dd>
                  <code>{asset.id}</code>
                </dd>
                <dt>SHA-256</dt>
                <dd>
                  <code>{asset.sha256}</code>
                </dd>
                <dt>Type / bytes</dt>
                <dd>
                  {asset.media_type} · {asset.size}
                </dd>
              </dl>
            </li>
          ))}
        </ul>
        {!board.assets?.length && (
          <p className="preparation-note">No assets were referenced by this published revision.</p>
        )}
      </details>
      <details>
        <summary>Preparation contributor history</summary>
        <p className="preparation-note">
          These identities cannot approve this preparation. Removing current editor access does not
          erase contribution history.
        </p>
        <ul>
          {(board.contributors ?? []).map((actor) => (
            <li key={actor}>
              <code>{actor}</code>
            </li>
          ))}
        </ul>
      </details>
      <button
        type="button"
        onClick={() => exportJson(board.scenario, `board-${board.id}-published-source.json`)}
      >
        Export pinned scenario
      </button>
    </aside>
  )
}

export function PreviewDetails({ preview }: { preview: Preview }) {
  const manifest = preview.manifest
  const liveCodes = new Set([
    'delivery_unverified',
    'target_contents_unverified',
    'connectivity_unverified',
    'permissions_unverified',
    'live_readiness_unverified',
    'result_binding_unverified',
  ])
  const staticFindings = preview.findings.filter((finding) => !liveCodes.has(finding.code))
  const liveFindings = preview.findings.filter((finding) => liveCodes.has(finding.code))
  return (
    <section
      className="glass preparation-panel"
      aria-label={`Immutable preview ${preview.sequence}`}
    >
      <div className="section-heading">
        <div>
          <h2>Preview {preview.sequence}</h2>
          <span className="pill">Saved board version {preview.board_version}</span>
        </div>
        <button
          type="button"
          onClick={() => exportJson(preview, `preparation-preview-${preview.sequence}.json`)}
        >
          Export immutable preview
        </button>
      </div>
      <p className="preparation-note">
        Frozen {new Date(preview.created_at).toLocaleString()} by <code>{preview.created_by}</code>.
        {preview.is_current
          ? ' Matches the current saved board version.'
          : ' Historical snapshot; not the current saved preparation.'}
      </p>
      <h3>Server-computed SHA-256 digest</h3>
      <code className="preparation-digest">{preview.digest}</code>
      <section className="preparation-section">
        <h3>Missing inputs and static constraints</h3>
        {!staticFindings.length && (
          <p>No additional static findings were reported. Live readiness is still unverified.</p>
        )}
        <ul className="preparation-list">
          {staticFindings.map((finding, index) => (
            <li key={`${finding.code}/${finding.step_id ?? ''}/${index}`}>
              <span className={`pill ${finding.severity === 'blocker' ? 'danger' : ''}`}>
                {finding.severity}
              </span>
              <p>{finding.message}</p>
              <code>
                {finding.code}
                {finding.path ? ` · ${finding.path}` : ''}
              </code>
              {finding.step_id && (
                <p className="preparation-note">
                  Step: <code>{finding.step_id}</code>
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>
      <section className="preparation-section">
        <h3>Unverified live prerequisites</h3>
        <p className="notice">
          Target contents, connectivity, target permissions, and notification delivery have not been
          checked. Described IDs and labels are not evidence. Execution remains disabled regardless
          of static findings or preparation approval.
        </p>
        <ul className="preparation-list">
          {liveFindings.map((finding, index) => (
            <li key={`${finding.code}/${index}`}>
              <p>{finding.message}</p>
              <code>
                {finding.code}
                {finding.path ? ` · ${finding.path}` : ''}
              </code>
            </li>
          ))}
        </ul>
      </section>
      <section className="preparation-section">
        <h3>Frozen bounds and recovery</h3>
        <dl className="preparation-meta">
          <dt>Proposed time window</dt>
          <dd>
            {manifest.draft.window
              ? `${manifest.draft.window.starts_at} → ${manifest.draft.window.ends_at}`
              : 'Unresolved'}
          </dd>
          <dt>Notification budget</dt>
          <dd>{manifest.draft.notification_budget ?? 0}</dd>
          <dt>Recovery policy</dt>
          <dd>{manifest.draft.recovery || 'Unresolved'}</dd>
        </dl>
      </section>
      <section className="preparation-section">
        <h3>Frozen steps and supplied values</h3>
        <ul className="preparation-list">
          {(manifest.draft.steps ?? []).map((step) => (
            <li key={step.id}>
              <h3>{step.label}</h3>
              <span className="pill">{step.kind}</span>
              <dl className="preparation-meta">
                <dt>Step ID</dt>
                <dd>
                  <code>{step.id}</code>
                </dd>
                <dt>Dependencies</dt>
                <dd>{step.depends_on?.length ? step.depends_on.join(', ') : 'None'}</dd>
                {step.kind === 'operation' && (
                  <>
                    <dt>Operation version</dt>
                    <dd>
                      {step.binding
                        ? `${step.binding.operation_key} @ ${step.binding.operation_version}`
                        : 'Unresolved'}
                    </dd>
                    <dt>Configuration revision ID</dt>
                    <dd>
                      <code>{step.binding?.configuration_id || 'Unresolved'}</code>
                    </dd>
                    <dt>Supplied typed values</dt>
                    <dd>
                      {Object.entries(step.parameters ?? {}).map(([name, value]) => (
                        <p key={name} className="preparation-note">
                          <code>{name}</code>:{' '}
                          {isResultReference(value) ? (
                            <>
                              prior declared result <code>{value.field}</code> from{' '}
                              {manifest.draft.steps?.find(
                                (source) => source.id === value.source_step_id,
                              )?.label || value.source_step_id}
                              . This reference is not an observed value.
                            </>
                          ) : value === null ? (
                            'Unresolved'
                          ) : (
                            <code>{JSON.stringify(value)}</code>
                          )}
                        </p>
                      ))}
                      <pre>{JSON.stringify(step.parameters ?? {}, null, 2)}</pre>
                    </dd>
                  </>
                )}
                {step.kind === 'wait' && (
                  <>
                    <dt>Wait limit</dt>
                    <dd>{step.wait_seconds ?? 'Unresolved'} seconds</dd>
                  </>
                )}
                {step.kind === 'condition' && (
                  <>
                    <dt>Typed comparison and branches</dt>
                    <dd>
                      <pre>{JSON.stringify(step.condition, null, 2)}</pre>
                    </dd>
                  </>
                )}
              </dl>
            </li>
          ))}
        </ul>
        {!manifest.draft.steps?.length && <p>No proposed steps in this snapshot.</p>}
      </section>
      <section className="preparation-section">
        <h3>Frozen target configurations</h3>
        {(manifest.configurations ?? []).map((configuration) => (
          <details key={configuration.id}>
            <summary>
              {configuration.connection_name} · configuration {configuration.version}
            </summary>
            <code className="preparation-digest">{configuration.digest}</code>
            <p className="preparation-note">
              Environment inventory: {configuration.environment_name} · resource classification:{' '}
              {configuration.content.classification}. Environment naming alone does not establish
              readiness.
            </p>
            {configuration.template_asset && (
              <p className="preparation-note">
                Immutable template: {configuration.template_asset.name} · version{' '}
                <code>{configuration.template_asset.id}</code> · SHA-256{' '}
                <code>{configuration.template_asset.sha256}</code>
              </p>
            )}
            <ConfigurationDetails content={configuration.content} />
          </details>
        ))}
        {!manifest.configurations?.length && <p>No target configurations were bound.</p>}
      </section>
      <details className="preparation-section">
        <summary>Complete immutable manifest and provenance</summary>
        <pre>{JSON.stringify(manifest, null, 2)}</pre>
      </details>
    </section>
  )
}

export function approvalLabel(approval: Approval, now: number) {
  if (approval.revoked_at) return 'Preparation decision revoked'
  if (Date.parse(approval.expires_at) <= now) return 'Preparation decision expired'
  if (!approval.validity.valid) return 'Preparation decision no longer valid'
  return approval.decision === 'approved'
    ? 'Preparation approved — not authorized to execute'
    : 'Preparation rejected'
}

export function ApprovalHistory({
  approvals,
  me,
  now,
  pending,
  onRevoke,
}: {
  approvals: Approval[]
  me?: Me
  now: number
  pending: boolean
  onRevoke: (approval: Approval) => void
}) {
  return (
    <section className="glass preparation-panel">
      <h2>Preparation approval history</h2>
      <p className="preparation-note">
        Decisions pin an exact preview and digest. Current validity is separate from immutable
        decision history and is rechecked by the service.
      </p>
      {!approvals.length && <p>No preparation decisions have been recorded.</p>}
      <ul className="preparation-list preparation-history">
        {approvals.map((approval) => (
          <li key={approval.id}>
            <h3>{approvalLabel(approval, now)}</h3>
            <span className="pill">{approval.decision}</span>
            <dl className="preparation-meta">
              <dt>Reviewer</dt>
              <dd>
                <code>{approval.reviewer}</code>
              </dd>
              <dt>Preview / board version</dt>
              <dd>
                <code>{approval.preview_id}</code> · {approval.board_version}
              </dd>
              <dt>Reviewed digest</dt>
              <dd>
                <code>{approval.digest}</code>
              </dd>
              <dt>Decision time</dt>
              <dd>{new Date(approval.created_at).toLocaleString()}</dd>
              <dt>Explicit expiry</dt>
              <dd>
                {new Date(approval.expires_at).toLocaleString()} ·{' '}
                <code>{approval.expires_at}</code>
              </dd>
              <dt>Reviewer note</dt>
              <dd>{approval.note || 'No note recorded'}</dd>
              <dt>Unverified acknowledgement</dt>
              <dd>{approval.acknowledge_unverified ? 'Acknowledged' : 'Not acknowledged'}</dd>
              <dt>Execution authorization</dt>
              <dd>None. This is a preparation-only decision.</dd>
            </dl>
            {approval.validity.reasons?.length > 0 && (
              <div className="notice">
                <strong>Current validity findings</strong>
                <ul>
                  {approval.validity.reasons.map((reason) => (
                    <li key={reason}>{preparationReason(reason)}</li>
                  ))}
                </ul>
              </div>
            )}
            {approval.revoked_at && (
              <p>
                Revoked {new Date(approval.revoked_at).toLocaleString()} by{' '}
                <code>{approval.revoked_by}</code>.
              </p>
            )}
            {!approval.revoked_at &&
              (me?.object_id === approval.reviewer || me?.organization_admin) && (
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => {
                    if (
                      window.confirm(
                        'Revoke this preparation decision? Its immutable history will be retained.',
                      )
                    )
                      onRevoke(approval)
                  }}
                >
                  Revoke decision {approval.sequence}
                </button>
              )}
          </li>
        ))}
      </ul>
    </section>
  )
}
