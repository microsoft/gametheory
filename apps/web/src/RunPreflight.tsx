import type { components } from './api.generated'
import {
  AttentionItem,
  BlockerChecklist,
  ReadyItem,
  TargetIdentity,
  localTime,
  readinessCoverage,
} from './runGuidance'
import { stepLabel, type SetupModel, type SetupOrigins } from './runSetup'

type Schemas = components['schemas']
export type RunPreflight = Schemas['RunPreflightView']
type Issue = Schemas['ExecutionIssue']

function issueTarget(issue: Issue, origins: SetupOrigins, model: SetupModel) {
  if (issue.section === 'preparation')
    return {
      title: issue.step_id ? `Preparation · ${stepLabel(model, issue.step_id)}` : 'Preparation',
      focus: undefined,
    }
  const index = issue.index ?? -1
  if (issue.section === 'observations')
    return { title: `Check ${index + 1}`, focus: origins.observations[index] }
  if (issue.section === 'objectives') {
    const id = origins.objectives[index]
    return {
      title: `Goal · ${model.objectives.find((item) => item.id === id)?.title ?? 'unavailable'}`,
      focus: id,
    }
  }
  const step = origins.recovery[index]
  return { title: `Undo · ${step ? stepLabel(model, step) : 'unavailable'}`, focus: step }
}

export function PreflightResult({
  result,
  model,
  origins,
  admin,
  onOpenPreparation,
  onFocus,
}: {
  result: RunPreflight
  model: SetupModel
  origins: SetupOrigins
  admin: boolean
  onOpenPreparation: () => void
  onFocus: (section: Issue['section'], key: string) => void
}) {
  const attention = result.issues.length + result.blockers.length
  const overBudget = result.planned_attempts > result.max_operations
  return (
    <div className="preflight" aria-live="polite">
      {!attention ? (
        <p className="notice success" role="status">
          Ready to create. Creating the run pins these inputs; it doesn’t contact any target. You
          will still authorize it{result.approval_required ? ', get independent approval,' : ''} and
          start it separately.
        </p>
      ) : result.valid ? (
        <p className="notice" role="status">
          This setup is valid, so you can create the run now. It can’t start until the{' '}
          {result.blockers.length === 1 ? 'item' : `${result.blockers.length} items`} below{' '}
          {result.blockers.length === 1 ? 'is' : 'are'} resolved.
        </p>
      ) : (
        <p className="notice error" role="status">
          Fix{' '}
          {result.issues.length === 1
            ? 'this setup issue'
            : `these ${result.issues.length} setup issues`}{' '}
          before creating the run.
        </p>
      )}
      <dl className="preparation-meta">
        <dt>Window</dt>
        <dd>
          {localTime(result.window_starts_at)} → {localTime(result.window_ends_at)}
        </dd>
        <dt>Start</dt>
        <dd>
          {result.trigger === 'scheduled'
            ? 'Automatically at the window start'
            : 'When an operator presses Start'}
        </dd>
        <dt>Approval</dt>
        <dd>
          {result.approval_required === null
            ? 'Unresolved: an environment has no usable policy'
            : result.approval_required
              ? 'Independent execution approval is required'
              : 'Not required by the environment policy'}
        </dd>
        <dt>Planned attempts</dt>
        <dd className={overBudget ? 'error-text' : undefined}>
          Up to {result.planned_attempts.toLocaleString()} of{' '}
          {result.max_operations.toLocaleString()}
          {overBudget ? '. The run would stop when the budget is used up.' : ''}
        </dd>
      </dl>
      {attention > 0 && <h3>Needs attention</h3>}
      {result.issues.length > 0 && (
        <ul className="checklist" aria-label="Setup issues">
          {result.issues.map((issue, index) => {
            const target = issueTarget(issue, origins, model)
            return (
              <AttentionItem key={`${issue.code}/${index}`}>
                <strong>{target.title}</strong>
                <p>{issue.message}</p>
                {issue.section === 'preparation' ? (
                  <button type="button" className="link-button" onClick={onOpenPreparation}>
                    Open the Preparation tab
                  </button>
                ) : (
                  target.focus && (
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => onFocus(issue.section, target.focus!)}
                    >
                      Go to {target.title.toLowerCase()}
                    </button>
                  )
                )}
              </AttentionItem>
            )
          })}
        </ul>
      )}
      <BlockerChecklist
        blockers={result.blockers}
        admin={admin}
        onOpenPreparation={onOpenPreparation}
      />
      <h3>Environments</h3>
      <ul className="checklist">
        {result.environments.map((environment) => {
          const label = `${environment.name} · ${environment.classification} · policy version ${environment.version}`
          return environment.execution_enabled ? (
            <ReadyItem key={environment.environment_id}>
              <strong>{label}</strong>
              <p>
                Execution is enabled.{' '}
                {environment.approval_required
                  ? 'Independent approval is required.'
                  : 'Approval is not required.'}
              </p>
            </ReadyItem>
          ) : (
            <AttentionItem key={environment.environment_id}>
              <strong>{label}</strong>
              <p>Execution is not enabled for this environment.</p>
            </AttentionItem>
          )
        })}
        {!result.environments.length && (
          <AttentionItem>
            <strong>No environment policy could be read.</strong>
          </AttentionItem>
        )}
      </ul>
      <h3>Targets and readiness</h3>
      <ul className="checklist">
        {result.targets.map((target) => {
          const coverage = readinessCoverage(
            target.readiness ?? target.latest_readiness,
            result.window_ends_at,
          )
          const ready = !!target.authority && !!target.readiness && coverage.covers
          const Item = ready ? ReadyItem : AttentionItem
          return (
            <Item key={target.configuration_id}>
              <strong>
                {target.connection_name} · {target.connection_kind.toUpperCase()} ·{' '}
                {target.environment_name}
              </strong>
              {target.authority ? (
                <TargetIdentity authority={target.authority} />
              ) : (
                <p>No operator-approved target binding was resolved.</p>
              )}
              <p>{coverage.text}</p>
            </Item>
          )
        })}
      </ul>
      <h3>Undo coverage</h3>
      {result.recovery.length ? (
        <ul className="checklist">
          {result.recovery.map((item) =>
            item.mode === 'automatic' ? (
              <ReadyItem key={item.step_id}>
                <strong>{item.label}</strong>
                <p>Undone automatically with recorded ownership and version checks.</p>
              </ReadyItem>
            ) : (
              <AttentionItem key={item.step_id}>
                <strong>{item.label}</strong>
                <p>An operator must account for this change manually after the run.</p>
              </AttentionItem>
            ),
          )}
        </ul>
      ) : (
        <p className="preparation-note">This run makes no changes that need undoing.</p>
      )}
      <p className="preparation-note">
        Checked {localTime(result.checked_at)}. This is not execution authority; authorization and
        every action recheck policy, access, readiness, and the window.
      </p>
    </div>
  )
}
