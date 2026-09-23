import { ConfigurationDetails } from './ConfigurationDetails'
import { invocationLabel } from './Catalog'
import { ScalarParameter } from './ScalarParameters'
import {
  bindingKey,
  boundOperation,
  isResultReference,
  localDateTime,
  newPreparationStep,
  parameterValue,
  timezoneDateTime,
  type BoardDraft,
  type BoardView,
  type Configuration,
  type PreparationStep,
} from './preparation'

export function BoardEditor({
  draft,
  scenario,
  configurations,
  editable,
  disabled = false,
  onChange,
}: {
  draft: BoardDraft
  scenario: BoardView['scenario']
  configurations: Configuration[]
  editable: boolean
  disabled?: boolean
  onChange: (draft: BoardDraft) => void
}) {
  const steps = draft.steps ?? []
  function updateStep(id: string, step: PreparationStep) {
    onChange({ ...draft, steps: steps.map((existing) => (existing.id === id ? step : existing)) })
  }
  function removeStep(id: string) {
    if (
      !window.confirm(
        'Remove this step and its dependency and branch references? Any condition using its result will need a new source.',
      )
    )
      return
    onChange({
      ...draft,
      steps: steps
        .filter((step) => step.id !== id)
        .map((step) => ({
          ...step,
          depends_on: step.depends_on?.filter((dependency) => dependency !== id),
          condition:
            step.condition?.source_step_id === id
              ? null
              : step.condition
                ? {
                    ...step.condition,
                    if_true: step.condition.if_true?.filter((target) => target !== id),
                    if_false: step.condition.if_false?.filter((target) => target !== id),
                  }
                : null,
        })),
    })
  }
  return (
    <section className="glass preparation-panel" aria-labelledby="preparation-editor-heading">
      <h2 id="preparation-editor-heading">Preparation draft</h2>
      {!editable && (
        <p className="notice">
          Read-only preparation. Editing requires current workspace editor access.
        </p>
      )}
      <p className="preparation-note">
        Describe proposed operations explicitly. Scenario flow labels are not compiled into actions.
        Dependencies and branches, not list order, define the proposed graph.
      </p>
      <fieldset disabled={!editable || disabled} className="preparation-inputs">
        <legend>Board identity and bounds</legend>
        <label>
          Board name
          <input
            value={draft.name}
            maxLength={160}
            required
            onChange={(event) => onChange({ ...draft, name: event.target.value })}
          />
        </label>
        <div className="preparation-fields">
          <label>
            Notification budget
            <input
              type="number"
              min={0}
              max={100}
              step={1}
              value={draft.notification_budget ?? ''}
              onChange={(event) =>
                onChange({
                  ...draft,
                  notification_budget: event.target.value === '' ? 0 : Number(event.target.value),
                })
              }
            />
            <span className="preparation-note">
              Maximum proposed messages, including escalations. Zero permits none. Sending remains
              disabled.
            </span>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={!!draft.window}
              onChange={(event) =>
                onChange({
                  ...draft,
                  window: event.target.checked ? { starts_at: '', ends_at: '' } : null,
                })
              }
            />
            Supply an explicit proposed time window
          </label>
          {draft.window && (
            <>
              <label>
                Window starts (local time)
                <input
                  type="datetime-local"
                  required
                  value={localDateTime(draft.window.starts_at)}
                  onChange={(event) =>
                    onChange({
                      ...draft,
                      window: { ...draft.window!, starts_at: timezoneDateTime(event.target.value) },
                    })
                  }
                />
              </label>
              <label>
                Window ends (local time)
                <input
                  type="datetime-local"
                  required
                  value={localDateTime(draft.window.ends_at)}
                  onChange={(event) =>
                    onChange({
                      ...draft,
                      window: { ...draft.window!, ends_at: timezoneDateTime(event.target.value) },
                    })
                  }
                />
              </label>
              <p className="preparation-note full">
                Timezone: {Intl.DateTimeFormat().resolvedOptions().timeZone}. The snapshot records
                explicit UTC timestamps: {draft.window.starts_at || 'unresolved'} →{' '}
                {draft.window.ends_at || 'unresolved'}. A time window is limited to seven days and
                is a proposed bound, not scheduled execution.
              </p>
            </>
          )}
          {!draft.window && (
            <p className="preparation-note full">
              Time window unresolved. The preview will report this missing prerequisite.
            </p>
          )}
          <label className="full">
            Recovery policy
            <textarea
              value={draft.recovery ?? ''}
              maxLength={4000}
              onChange={(event) => onChange({ ...draft, recovery: event.target.value })}
            />
            <span className="preparation-note">
              Describe the intended recovery choice, ownership/version checks, retained evidence,
              and known irreversible effects. No recovery is performed here.
            </span>
          </label>
        </div>
      </fieldset>
      <div className="section-heading preparation-section">
        <div>
          <h2>Proposed steps</h2>
          <p className="preparation-note">
            {steps.length} of 200 explicit steps. Waits are bounded to 1–86,400 seconds; cycles are
            not permitted.
          </p>
        </div>
        {editable && (
          <div className="toolbar">
            <button
              type="button"
              disabled={disabled || steps.length >= 200}
              onClick={() =>
                onChange({ ...draft, steps: [...steps, newPreparationStep('operation')] })
              }
            >
              Add operation
            </button>
            <button
              type="button"
              disabled={disabled || steps.length >= 200}
              onClick={() =>
                onChange({ ...draft, steps: [...steps, newPreparationStep('condition')] })
              }
            >
              Add condition
            </button>
            <button
              type="button"
              disabled={disabled || steps.length >= 200}
              onClick={() => onChange({ ...draft, steps: [...steps, newPreparationStep('wait')] })}
            >
              Add bounded wait
            </button>
          </div>
        )}
      </div>
      {!steps.length && (
        <p className="notice">
          No preparation steps yet. Add operations from registered catalogs, or an explicit
          condition or bounded wait. Nothing is inferred from the published scenario diagram.
        </p>
      )}
      <div>
        {steps.map((step, index) => {
          const bound = boundOperation(step, configurations)
          return (
            <section
              className="preparation-step"
              key={step.id}
              aria-label={`Preparation step ${index + 1}`}
            >
              <div className="section-heading">
                <div>
                  <h3>
                    {index + 1}. {step.label || `Untitled ${step.kind}`}
                  </h3>
                  <span className="pill">{step.kind}</span>
                </div>
                {editable && (
                  <button type="button" disabled={disabled} onClick={() => removeStep(step.id)}>
                    Remove step {index + 1}
                  </button>
                )}
              </div>
              <fieldset disabled={!editable || disabled} className="preparation-inputs">
                <legend>Step {index + 1} details</legend>
                <div className="preparation-fields">
                  <label>
                    Step {index + 1} label
                    <input
                      required
                      value={step.label}
                      maxLength={160}
                      onChange={(event) =>
                        updateStep(step.id, { ...step, label: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    Authoring node reference
                    <select
                      value={step.authoring_node_id ?? ''}
                      onChange={(event) =>
                        updateStep(step.id, {
                          ...step,
                          authoring_node_id: event.target.value || null,
                        })
                      }
                    >
                      <option value="">None — independently authored step</option>
                      {(scenario.content.nodes ?? []).map((node) => (
                        <option key={node.id} value={node.id}>
                          {node.label} · {node.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  {step.kind === 'operation' && (
                    <label className="full">
                      Registered operation and configuration
                      <select
                        value={bindingKey(step.binding)}
                        onChange={(event) => {
                          if (
                            Object.keys(step.parameters ?? {}).length &&
                            !window.confirm(
                              'Change the operation binding and discard its supplied parameter values?',
                            )
                          )
                            return
                          const selected = configurations
                            .flatMap((configuration) =>
                              (configuration.content.catalog.operations ?? []).map((operation) => ({
                                configuration_id: configuration.id,
                                operation_key: operation.key,
                                operation_version: operation.version,
                              })),
                            )
                            .find((binding) => bindingKey(binding) === event.target.value)
                          updateStep(step.id, {
                            ...step,
                            binding: selected ?? null,
                            parameters: {},
                          })
                        }}
                      >
                        <option value="">Unresolved — choose a registered operation</option>
                        {step.binding && (!bound || bound.configuration.withdrawn_at) && (
                          <option value={bindingKey(step.binding)} disabled>
                            {bound?.configuration.withdrawn_at ? 'Withdrawn' : 'Unavailable'}{' '}
                            binding: {step.binding.operation_key} @ {step.binding.operation_version}
                          </option>
                        )}
                        {configurations
                          .filter((configuration) => !configuration.withdrawn_at)
                          .map((configuration) => (
                            <optgroup
                              key={configuration.id}
                              label={`${configuration.connection_name} · configuration ${configuration.version} · ${configuration.content.classification}`}
                            >
                              {(configuration.content.catalog.operations ?? []).map((operation) => {
                                const binding = {
                                  configuration_id: configuration.id,
                                  operation_key: operation.key,
                                  operation_version: operation.version,
                                }
                                return (
                                  <option key={bindingKey(binding)} value={bindingKey(binding)}>
                                    {operation.label} · {operation.key} @ {operation.version}
                                  </option>
                                )
                              })}
                            </optgroup>
                          ))}
                      </select>
                    </label>
                  )}
                  {step.kind === 'wait' && (
                    <label className="full">
                      Bounded wait in seconds
                      <input
                        type="number"
                        required
                        min={1}
                        max={86400}
                        step={1}
                        value={step.wait_seconds ?? ''}
                        onChange={(event) =>
                          updateStep(step.id, {
                            ...step,
                            wait_seconds:
                              event.target.value === '' ? null : Number(event.target.value),
                          })
                        }
                      />
                      <span className="preparation-note">
                        One finite wait, not an unbounded poll, retry, or schedule.
                      </span>
                    </label>
                  )}
                </div>
                {step.kind === 'operation' && bound && (
                  <>
                    <dl className="preparation-meta">
                      <dt>Proposed effect</dt>
                      <dd>
                        {bound.operation.effect} · <code>{invocationLabel(bound.operation)}</code>
                      </dd>
                      <dt>Target resource</dt>
                      <dd>
                        <code>{bound.configuration.content.resource_id || 'Unresolved'}</code>
                      </dd>
                      <dt>Configuration</dt>
                      <dd>
                        {bound.configuration.connection_name} · revision{' '}
                        {bound.configuration.version} · {bound.configuration.content.classification}
                      </dd>
                      <dt>Environment inventory</dt>
                      <dd>{bound.configuration.environment_name}</dd>
                      <dt>Recovery description</dt>
                      <dd>{bound.operation.recovery}</dd>
                    </dl>
                    <fieldset className="preparation-fields">
                      <legend>Typed scalar parameters</legend>
                      {(bound.operation.parameters ?? []).map((field) => (
                        <OperationParameter
                          key={field.name}
                          field={field}
                          step={step}
                          steps={steps}
                          configurations={configurations}
                          editable={editable && !disabled}
                          opaqueVersion={
                            bound.operation.invocation.kind === 'rest' &&
                            field.name === 'expected_version'
                          }
                          dispatcherOwned={
                            bound.operation.invocation.kind === 'sql' &&
                            field.name === 'idempotency_key'
                          }
                          onChange={(value) => updateStep(step.id, value)}
                        />
                      ))}
                      {!bound.operation.parameters?.length && (
                        <p className="preparation-note">No parameters declared.</p>
                      )}
                    </fieldset>
                  </>
                )}
                {step.kind === 'operation' && !bound && (
                  <p className="notice">
                    No available operation binding. Save and preview to record this as a missing
                    prerequisite, or choose an active configuration.
                  </p>
                )}
                {step.kind === 'condition' && (
                  <ConditionEditor
                    step={step}
                    steps={steps}
                    configurations={configurations}
                    editable={editable}
                    onChange={(value) => updateStep(step.id, value)}
                  />
                )}
                <details>
                  <summary>Explicit dependencies ({step.depends_on?.length ?? 0})</summary>
                  <StepChoices
                    steps={steps}
                    exclude={step.id}
                    selected={step.depends_on ?? []}
                    label="Depends on"
                    onChange={(depends_on) => updateStep(step.id, { ...step, depends_on })}
                  />
                </details>
              </fieldset>
              {bound && (
                <details>
                  <summary>
                    Inspect target, notification recipients, catalog, and configuration digest
                  </summary>
                  <code className="preparation-digest">{bound.configuration.digest}</code>
                  {bound.configuration.template_asset && (
                    <p className="preparation-note">
                      Immutable template: {bound.configuration.template_asset.name} · version{' '}
                      <code>{bound.configuration.template_asset.id}</code> · SHA-256{' '}
                      <code>{bound.configuration.template_asset.sha256}</code>
                    </p>
                  )}
                  {bound.configuration.withdrawn_at && (
                    <p className="notice error">
                      This configuration was withdrawn. Existing history is preserved; current
                      approval cannot use it.
                    </p>
                  )}
                  <ConfigurationDetails content={bound.configuration.content} />
                </details>
              )}
              <p className="preparation-note">
                Step ID: <code>{step.id}</code>
              </p>
            </section>
          )
        })}
      </div>
    </section>
  )
}

function OperationParameter({
  field,
  step,
  steps,
  configurations,
  editable,
  onChange,
  opaqueVersion,
  dispatcherOwned,
}: {
  field: import('./Catalog').OperationField
  step: PreparationStep
  steps: PreparationStep[]
  configurations: Configuration[]
  editable: boolean
  onChange: (step: PreparationStep) => void
  opaqueVersion: boolean
  dispatcherOwned: boolean
}) {
  const value = parameterValue(step, field.name)
  if (dispatcherOwned)
    return (
      <fieldset>
        <legend>
          {field.name} · {field.type}
        </legend>
        <p className="preparation-note">
          Supplied automatically during exercise runs: the executor derives a stable key for each
          step and attempt. Leave this empty. The static preview lists it as unresolved, which is
          expected.
        </p>
        {value != null && (
          <div className="notice" role="status">
            A typed value here would block run creation.{' '}
            {editable && (
              <button
                type="button"
                onClick={() =>
                  onChange({
                    ...step,
                    parameters: Object.fromEntries(
                      Object.entries(step.parameters ?? {}).filter(([name]) => name !== field.name),
                    ),
                  })
                }
              >
                Clear {field.name}
              </button>
            )}
          </div>
        )}
      </fieldset>
    )
  const reference = isResultReference(value) ? value : undefined
  const literal = isResultReference(value) ? undefined : (value ?? undefined)
  const sources = steps
    .filter((candidate) => candidate.kind === 'operation' && candidate.id !== step.id)
    .flatMap((source) =>
      (boundOperation(source, configurations)?.operation.results ?? [])
        .filter((result) => result.type === field.type && result.required !== false)
        .map((result) => ({ source, result })),
    )
  return (
    <fieldset>
      <legend>
        {field.name} · {field.type}
      </legend>
      <label>
        Value source for {field.name}
        <select
          disabled={!editable}
          value={reference ? 'result' : 'literal'}
          onChange={(event) => {
            if (
              value != null &&
              !window.confirm('Replace the current parameter input with a different value source?')
            )
              return
            onChange({
              ...step,
              parameters: {
                ...step.parameters,
                [field.name]:
                  event.target.value === 'result' ? { source_step_id: '', field: '' } : null,
              },
            })
          }}
        >
          <option value="literal">Literal scalar value</option>
          <option value="result">Typed prior operation result</option>
        </select>
      </label>
      {reference ? (
        <>
          <label>
            Prior result for {field.name}
            <select
              disabled={!editable}
              value={
                reference.source_step_id ? `${reference.source_step_id}/${reference.field}` : ''
              }
              onChange={(event) => {
                const selected = sources.find(
                  ({ source, result }) => `${source.id}/${result.name}` === event.target.value,
                )
                onChange({
                  ...step,
                  depends_on: selected
                    ? [...new Set([...(step.depends_on ?? []), selected.source.id])]
                    : step.depends_on,
                  parameters: {
                    ...step.parameters,
                    [field.name]: selected
                      ? { source_step_id: selected.source.id, field: selected.result.name }
                      : { source_step_id: '', field: '' },
                  },
                })
              }}
            >
              <option value="">Choose a declared {field.type} result</option>
              {reference.source_step_id &&
                !sources.some(
                  ({ source, result }) =>
                    source.id === reference.source_step_id && result.name === reference.field,
                ) && (
                  <option value={`${reference.source_step_id}/${reference.field}`} disabled>
                    Unavailable or optional result: {reference.field}
                  </option>
                )}
              {sources.map(({ source, result }) => (
                <option key={`${source.id}/${result.name}`} value={`${source.id}/${result.name}`}>
                  {source.label || 'Untitled operation'} · {result.name} ({result.type})
                </option>
              ))}
            </select>
          </label>
          <p className="preparation-note">
            A declaration, not an observed value. The source is added as an explicit dependency.
            Only declared, required outputs of the same type are selectable. The service verifies
            that the source is guaranteed to precede this step, not just a branch sibling. No future
            record identifier is invented.
          </p>
          {!sources.length && (
            <p className="notice">
              Bind an operation with a declared, required {field.type} result before using this
              parameter source.
            </p>
          )}
        </>
      ) : (
        <ScalarParameter
          field={field}
          value={literal}
          disabled={!editable}
          opaqueVersion={opaqueVersion}
          onChange={(value) => {
            const parameters =
              value === undefined
                ? Object.fromEntries(
                    Object.entries(step.parameters ?? {}).filter(([name]) => name !== field.name),
                  )
                : { ...step.parameters, [field.name]: value }
            onChange({ ...step, parameters })
          }}
        />
      )}
    </fieldset>
  )
}

function ConditionEditor({
  step,
  steps,
  configurations,
  editable,
  onChange,
}: {
  step: PreparationStep
  steps: PreparationStep[]
  configurations: Configuration[]
  editable: boolean
  onChange: (step: PreparationStep) => void
}) {
  const condition = step.condition
  const source = steps.find((item) => item.id === condition?.source_step_id)
  const results = source
    ? (boundOperation(source, configurations)?.operation.results ?? []).filter(
        (field) => field.required !== false,
      )
    : []
  const result = results.find((field) => field.name === condition?.result_field)
  const operators = [
    ['eq', 'Equal to'],
    ['ne', 'Not equal to'],
    ['gt', 'Greater than'],
    ['gte', 'Greater than or equal to'],
    ['lt', 'Less than'],
    ['lte', 'Less than or equal to'],
  ] as const
  return (
    <div className="preparation-fields">
      <label>
        Earlier operation result source
        <select
          value={condition?.source_step_id ?? ''}
          onChange={(event) => {
            const id = event.target.value
            onChange({
              ...step,
              depends_on: id ? [...new Set([...(step.depends_on ?? []), id])] : step.depends_on,
              condition: id
                ? {
                    source_step_id: id,
                    result_field: '',
                    operator: 'eq',
                    value: '',
                    if_true: condition?.if_true ?? [],
                    if_false: condition?.if_false ?? [],
                  }
                : null,
            })
          }}
        >
          <option value="">Choose an operation with required results</option>
          {steps
            .filter((item) => item.id !== step.id && item.kind === 'operation')
            .map((item) => (
              <option
                key={item.id}
                value={item.id}
                disabled={
                  !boundOperation(item, configurations)?.operation.results?.some(
                    (field) => field.required !== false,
                  )
                }
              >
                {item.label || 'Untitled operation'}
              </option>
            ))}
        </select>
      </label>
      {condition && (
        <>
          <label>
            Declared result field
            <select
              value={condition.result_field}
              onChange={(event) =>
                onChange({
                  ...step,
                  condition: { ...condition, result_field: event.target.value, value: '' },
                })
              }
            >
              <option value="">Choose a declared result</option>
              {condition.result_field &&
                !results.some((field) => field.name === condition.result_field) && (
                  <option value={condition.result_field} disabled>
                    Unavailable or optional result: {condition.result_field}
                  </option>
                )}
              {results.map((field) => (
                <option key={field.name} value={field.name}>
                  {field.name} · {field.type}
                </option>
              ))}
            </select>
          </label>
          <label>
            Comparison
            <select
              value={condition.operator}
              onChange={(event) => {
                const operator = operators.find(([key]) => key === event.target.value)?.[0]
                if (operator) onChange({ ...step, condition: { ...condition, operator } })
              }}
            >
              {operators.map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {result && (
            <ScalarParameter
              field={result}
              value={condition.value === '' ? undefined : condition.value}
              disabled={!editable}
              context="Compare result"
              onChange={(value) =>
                onChange({ ...step, condition: { ...condition, value: value ?? '' } })
              }
            />
          )}
          <fieldset>
            <legend>If comparison is true</legend>
            <StepChoices
              steps={steps}
              exclude={step.id}
              selected={condition.if_true ?? []}
              label="True branch"
              onChange={(if_true) => onChange({ ...step, condition: { ...condition, if_true } })}
            />
          </fieldset>
          <fieldset>
            <legend>If comparison is false</legend>
            <StepChoices
              steps={steps}
              exclude={step.id}
              selected={condition.if_false ?? []}
              label="False branch"
              onChange={(if_false) => onChange({ ...step, condition: { ...condition, if_false } })}
            />
          </fieldset>
          <p className="preparation-note full">
            The source is an explicit dependency. Comparisons are typed values, not executable
            expressions. Select at least one branch target; true and false targets must be distinct.
            An empty individual branch ends that path.
          </p>
        </>
      )}
    </div>
  )
}

function StepChoices({
  steps,
  exclude,
  selected,
  label,
  onChange,
}: {
  steps: PreparationStep[]
  exclude: string
  selected: string[]
  label: string
  onChange: (ids: string[]) => void
}) {
  const choices = steps.filter((step) => step.id !== exclude)
  return (
    <div className="check-options">
      {choices.map((step) => (
        <label key={step.id} className="check">
          <input
            type="checkbox"
            aria-label={`${label}: ${step.label || step.kind}`}
            checked={selected.includes(step.id)}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? [...selected, step.id]
                  : selected.filter((id) => id !== step.id),
              )
            }
          />
          {step.label || `Untitled ${step.kind}`} <span className="pill">{step.kind}</span>
        </label>
      ))}
      {!choices.length && (
        <p className="preparation-note">Add another step to reference it here.</p>
      )}
    </div>
  )
}
