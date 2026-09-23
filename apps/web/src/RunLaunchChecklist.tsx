import type { components } from './api.generated'
import {
  AttentionItem,
  BlockerChecklist,
  ReadyItem,
  TargetIdentity,
  localTime,
  readinessCoverage,
} from './runGuidance'

type Run = components['schemas']['RunView']

export function plannedRunAttempts(run: Run) {
  const manifest = run.manifest
  const sampled = new Map(manifest.observations.map((item) => [item.step_id, item.max_samples]))
  return (
    (manifest.preparation.draft.steps ?? [])
      .filter((step) => step.kind === 'operation')
      .reduce((sum, step) => sum + (sampled.get(step.id) ?? 1), 0) + manifest.recovery.length
  )
}

export function LaunchChecklist({
  run,
  admin,
  authorizeHere,
}: {
  run: Run
  admin: boolean
  authorizeHere?: boolean
}) {
  const manifest = run.manifest
  const window = manifest.preparation.draft.window
  const configurations = manifest.preparation.configurations
  // Older responses carry only strings; keep them visible without inventing a remedy.
  const blockers =
    run.blocker_details ??
    run.blockers.map((message) => ({ code: 'unclassified', message, remedy: 'run_state' as const }))
  const planned = plannedRunAttempts(run)
  const limit = manifest.max_operations ?? 1000
  const writes = (manifest.preparation.draft.steps ?? []).filter((step) =>
    configurations.some(
      (configuration) =>
        configuration.id === step.binding?.configuration_id &&
        configuration.content.catalog.operations?.some(
          (operation) =>
            operation.key === step.binding?.operation_key &&
            operation.version === step.binding?.operation_version &&
            operation.effect === 'write',
        ),
    ),
  )
  const automatic = new Set(manifest.recovery.map((item) => item.step_id))
  const environments = [
    ...new Map(configurations.map((item) => [item.environment_id, item])).values(),
  ]
  return (
    <section className="glass preparation-panel" aria-labelledby="launch-checklist-heading">
      <h2 id="launch-checklist-heading">Launch checklist</h2>
      <p className="preparation-note">
        What this run will touch and what still stands in the way. Every action rechecks these
        conditions when it happens.
      </p>
      {blockers.length ? (
        <>
          <h3>Needs attention</h3>
          <BlockerChecklist blockers={blockers} admin={admin} authorizeHere={authorizeHere} />
        </>
      ) : (
        <p className="notice success" role="status">
          No current blockers. Accepted effects still can’t be undone by pausing or stopping.
        </p>
      )}
      <dl className="preparation-meta">
        <dt>Window</dt>
        <dd>
          {window ? `${localTime(window.starts_at)} → ${localTime(window.ends_at)}` : 'Unresolved'}
        </dd>
        <dt>Start</dt>
        <dd>
          {manifest.trigger === 'scheduled'
            ? 'Automatically at the window start, after it is started'
            : 'When the operator presses Start inside the window'}
        </dd>
        <dt>Approval</dt>
        <dd>
          {run.approval_status === 'unresolved'
            ? 'Unresolved environment policy'
            : run.approval_required
              ? `Independent approval ${run.approval_status.replaceAll('_', ' ')}`
              : 'Not required by the environment policy'}
        </dd>
        <dt>Planned attempts</dt>
        <dd className={planned > limit ? 'error-text' : undefined}>
          Up to {planned.toLocaleString()} of {limit.toLocaleString()}
        </dd>
      </dl>
      <h3>Environments</h3>
      <ul className="checklist">
        {run.authorization
          ? run.authorization.policies.map((policy) => (
              <ReadyItem key={policy.environment_id}>
                <strong>
                  {policy.name} · {policy.classification} · policy version {policy.version}
                </strong>
                <p>
                  Pinned when the run was authorized.{' '}
                  {policy.approval_required
                    ? 'Independent approval is required.'
                    : 'Approval is not required.'}
                </p>
              </ReadyItem>
            ))
          : environments.map((item) => (
              <AttentionItem key={item.environment_id}>
                <strong>
                  {item.environment_name} · labeled {item.content.classification}
                </strong>
                <p>Authorize the run to pin the environment’s current policy.</p>
              </AttentionItem>
            ))}
      </ul>
      <h3>Targets and readiness</h3>
      <ul className="checklist">
        {configurations.map((configuration) => {
          const authority = run.authorization?.targets.find(
            (item) => item.configuration_id === configuration.id,
          )
          const readiness = run.authorization?.readiness.find(
            (item) => item.configuration_id === configuration.id,
          )
          const coverage = readinessCoverage(readiness, window?.ends_at)
          const Item = authority && coverage.covers ? ReadyItem : AttentionItem
          return (
            <Item key={configuration.id}>
              <strong>
                {configuration.connection_name} · {configuration.connection_kind.toUpperCase()} ·{' '}
                {configuration.environment_name}
              </strong>
              {authority ? (
                <>
                  <TargetIdentity authority={authority} />
                  <p>{coverage.text}</p>
                </>
              ) : (
                <p>Identity and readiness are pinned when the run is authorized.</p>
              )}
            </Item>
          )
        })}
      </ul>
      <h3>Undo plan</h3>
      {writes.length ? (
        <ul className="checklist">
          {writes.map((step) =>
            automatic.has(step.id) ? (
              <ReadyItem key={step.id}>
                <strong>{step.label}</strong>
                <p>Undone automatically with recorded ownership and version checks.</p>
              </ReadyItem>
            ) : (
              <AttentionItem key={step.id}>
                <strong>{step.label}</strong>
                <p>An operator must account for this change manually.</p>
              </AttentionItem>
            ),
          )}
        </ul>
      ) : (
        <p className="preparation-note">This run makes no changes that need undoing.</p>
      )}
    </section>
  )
}
