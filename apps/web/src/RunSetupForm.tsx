import { useId, type ReactNode } from 'react'
import type { OperationField } from './Catalog'
import { ScalarParameter } from './ScalarParameters'
import {
  comparators,
  describeGoal,
  describeUndo,
  describeWatch,
  isDispatcherOwned,
  newWatch,
  resultField,
  suggestedUndo,
  supportsOrdering,
  type BoundStep,
  type Comparator,
  type GoalInput,
  type RunSetupDraft,
  type SetupModel,
  type SetupProblem,
  type UndoInput,
  type ValueInput,
  type WatchInput,
} from './runSetup'

type Change = (draft: RunSetupDraft) => void

export function watchId(key: string) {
  return `run-watch-${key}`
}
export function goalId(objectiveId: string) {
  return `run-goal-${objectiveId}`
}
export function undoId(stepId: string) {
  return `run-undo-${stepId}`
}

function stepName(item: BoundStep) {
  return `${item.step.label || 'Untitled operation'}${item.conditional ? ' (may not run)' : ''}`
}

function fieldName(field: OperationField) {
  return `${field.name} · ${field.type}${field.required === false ? ' · may be empty' : ''}`
}

function Problems({ problems, id }: { problems: SetupProblem[]; id: string }) {
  if (!problems.length) return null
  return (
    <ul id={id} className="setup-problems">
      {problems.map((problem) => (
        <li key={problem.message}>{problem.message}</li>
      ))}
    </ul>
  )
}

function Readback({ children }: { children: ReactNode }) {
  return <p className="setup-readback">{children}</p>
}

const units = [
  ['seconds', 1],
  ['minutes', 60],
  ['hours', 3600],
] as const

export function DurationField({
  label,
  seconds,
  maximumUnit = 'hours',
  disabled,
  onChange,
}: {
  label: string
  seconds: number | undefined
  maximumUnit?: 'minutes' | 'hours'
  disabled?: boolean
  onChange: (seconds: number | undefined) => void
}) {
  const id = useId()
  const available = units.filter(([name]) => maximumUnit === 'hours' || name !== 'hours')
  const [unit, size] =
    [...available].reverse().find(([, size]) => seconds !== undefined && seconds % size === 0) ??
    available[0]
  const amount = seconds === undefined ? '' : String(seconds / size)
  return (
    <div className="setup-duration">
      <label htmlFor={`${id}-amount`}>{label}</label>
      <div>
        <input
          id={`${id}-amount`}
          type="number"
          min={1}
          step={1}
          inputMode="numeric"
          value={amount}
          disabled={disabled}
          onChange={(event) => {
            const value = event.target.value === '' ? undefined : Number(event.target.value)
            onChange(value === undefined || !Number.isFinite(value) ? undefined : value * size)
          }}
        />
        <select
          aria-label={`${label} unit`}
          value={unit}
          disabled={disabled}
          onChange={(event) => {
            const next = available.find(([name]) => name === event.target.value)
            if (next && seconds !== undefined) onChange((seconds / size) * next[1])
          }}
        >
          {available.map(([name]) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}

function ComparatorField({
  label,
  field,
  value,
  disabled,
  onChange,
}: {
  label: string
  field: OperationField | undefined
  value: Comparator
  disabled?: boolean
  onChange: (value: Comparator) => void
}) {
  return (
    <label>
      {label}
      <select
        aria-label={label}
        value={value}
        disabled={disabled || !field}
        onChange={(event) => {
          const next = comparators.find(([key]) => key === event.target.value)?.[0]
          if (next) onChange(next)
        }}
      >
        {comparators
          .filter(([key]) => supportsOrdering(field) || key === 'eq' || key === 'ne')
          .map(([key, text]) => (
            <option key={key} value={key}>
              {text}
            </option>
          ))}
      </select>
    </label>
  )
}

export function RunSetupForm({
  model,
  draft,
  problems,
  disabled = false,
  onChange,
}: {
  model: SetupModel
  draft: RunSetupDraft
  problems: SetupProblem[]
  disabled?: boolean
  onChange: Change
}) {
  const window = model.window
  const scoped = (section: SetupProblem['section'], key: string) =>
    problems.filter((problem) => problem.section === section && problem.key === key)
  return (
    <div className="setup-form">
      <section className="setup-section" aria-labelledby="run-start-heading">
        <h3 id="run-start-heading">When the run starts</h3>
        <div className="setup-choices" role="radiogroup" aria-labelledby="run-start-heading">
          <label className="check">
            <input
              type="radio"
              name="run-trigger"
              checked={draft.trigger === 'manual'}
              disabled={disabled}
              onChange={() => onChange({ ...draft, trigger: 'manual' })}
            />
            <span>
              <strong>When an operator presses Start</strong>
              <small>Only inside the pinned window.</small>
            </span>
          </label>
          <label className="check">
            <input
              type="radio"
              name="run-trigger"
              checked={draft.trigger === 'scheduled'}
              disabled={disabled}
              onChange={() => onChange({ ...draft, trigger: 'scheduled' })}
            />
            <span>
              <strong>Automatically at the window start</strong>
              <small>A one-off schedule, used only after the run is authorized and started.</small>
            </span>
          </label>
        </div>
        <p className="preparation-note">
          {window
            ? `Pinned window: ${new Date(window.starts_at).toLocaleString()} → ${new Date(window.ends_at).toLocaleString()}.`
            : 'This preview has no time window. Add one in the Preparation tab before creating a run.'}
        </p>
      </section>

      <section className="setup-section" aria-labelledby="run-watch-heading">
        <div className="section-heading">
          <div>
            <h3 id="run-watch-heading">Watch for a condition</h3>
            <p className="preparation-note">
              Keep reading a step until its result matches, then continue. Only read steps can be
              repeated, and each one has a hard stop.
            </p>
          </div>
          <button
            type="button"
            disabled={disabled || !model.reads.length || draft.watches.length >= 100}
            onClick={() =>
              onChange({ ...draft, watches: [...draft.watches, newWatch(model, draft)] })
            }
          >
            Add a check
          </button>
        </div>
        {!model.reads.length && (
          <p className="preparation-note">
            This preparation has no read steps, so there is nothing to watch.
          </p>
        )}
        {draft.watches.map((watch, index) => (
          <WatchEditor
            key={watch.key}
            model={model}
            watch={watch}
            number={index + 1}
            problems={scoped('watch', watch.key)}
            disabled={disabled}
            onChange={(next) =>
              onChange({
                ...draft,
                watches: draft.watches.map((item) => (item.key === watch.key ? next : item)),
              })
            }
            onRemove={() =>
              onChange({
                ...draft,
                watches: draft.watches.filter((item) => item.key !== watch.key),
              })
            }
          />
        ))}
      </section>

      <section className="setup-section" aria-labelledby="run-goal-heading">
        <h3 id="run-goal-heading">How each goal is judged</h3>
        <p className="preparation-note">
          Goals come from the published scenario. A measured goal is met, unmet, or indeterminate
          from recorded evidence only; missing evidence is never counted as a failure.
        </p>
        {!draft.goals.length && (
          <p className="preparation-note">The published scenario has no goals to measure.</p>
        )}
        {draft.goals.map((goal) => (
          <GoalEditor
            key={goal.objective_id}
            model={model}
            goal={goal}
            problems={scoped('goal', goal.objective_id)}
            disabled={disabled}
            onChange={(next) =>
              onChange({
                ...draft,
                goals: draft.goals.map((item) =>
                  item.objective_id === goal.objective_id ? next : item,
                ),
              })
            }
          />
        ))}
      </section>

      <section className="setup-section" aria-labelledby="run-undo-heading">
        <h3 id="run-undo-heading">Undo plan</h3>
        <p className="preparation-note">
          Decide how each change is reversed after the exercise. Automatic undo runs only when the
          record still belongs to this run and nobody has changed it since.
        </p>
        {!draft.undo.length && (
          <p className="preparation-note">This preparation makes no changes that need undoing.</p>
        )}
        {draft.undo.map((undo) => (
          <UndoEditor
            key={undo.step_id}
            model={model}
            undo={undo}
            problems={scoped('undo', undo.step_id)}
            disabled={disabled}
            onChange={(next) =>
              onChange({
                ...draft,
                undo: draft.undo.map((item) => (item.step_id === undo.step_id ? next : item)),
              })
            }
          />
        ))}
      </section>
    </div>
  )
}

function WatchEditor({
  model,
  watch,
  number,
  problems,
  disabled,
  onChange,
  onRemove,
}: {
  model: SetupModel
  watch: WatchInput
  number: number
  problems: SetupProblem[]
  disabled: boolean
  onChange: (watch: WatchInput) => void
  onRemove: () => void
}) {
  const id = watchId(watch.key)
  const read = model.byId.get(watch.step_id)
  const field = resultField(model, watch.step_id, watch.field)
  return (
    <fieldset
      id={id}
      className="setup-check"
      disabled={disabled}
      aria-describedby={problems.length ? `${id}-problems` : undefined}
    >
      <legend>Check {number}</legend>
      <div className="preparation-fields">
        <label>
          Read step
          <select
            aria-label="Read step"
            value={watch.step_id}
            onChange={(event) =>
              onChange({ ...watch, step_id: event.target.value, field: '', value: undefined })
            }
          >
            <option value="">Choose a read step</option>
            {watch.step_id && !read && (
              <option value={watch.step_id} disabled>
                Unavailable step
              </option>
            )}
            {model.reads.map((item) => (
              <option key={item.step.id} value={item.step.id}>
                {stepName(item)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Reading
          <select
            aria-label="Reading"
            value={watch.field}
            disabled={!read}
            onChange={(event) =>
              onChange({ ...watch, field: event.target.value, operator: 'eq', value: undefined })
            }
          >
            <option value="">Choose a declared result</option>
            {(read?.operation.results ?? []).map((item) => (
              <option key={item.name} value={item.name}>
                {fieldName(item)}
              </option>
            ))}
          </select>
        </label>
        <ComparatorField
          label="Continue when the reading"
          field={field}
          value={watch.operator}
          onChange={(operator) => onChange({ ...watch, operator })}
        />
        {field ? (
          <ScalarParameter
            field={{ ...field, required: true, minimum: null, maximum: null, max_length: null }}
            value={watch.value}
            context="Compare with"
            requirement="Comparison value"
            clearable={false}
            onChange={(value) => onChange({ ...watch, value })}
          />
        ) : (
          <p className="preparation-note">Choose a reading to enter its comparison value.</p>
        )}
        <DurationField
          label="Check every"
          seconds={watch.interval_seconds}
          maximumUnit="minutes"
          onChange={(interval_seconds) => onChange({ ...watch, interval_seconds })}
        />
        <DurationField
          label="Give up after"
          seconds={watch.timeout_seconds}
          onChange={(timeout_seconds) => onChange({ ...watch, timeout_seconds })}
        />
        <label>
          At most this many checks
          <input
            aria-label="At most this many checks"
            type="number"
            min={1}
            max={1000}
            step={1}
            inputMode="numeric"
            value={watch.max_samples ?? ''}
            onChange={(event) =>
              onChange({
                ...watch,
                max_samples: event.target.value === '' ? undefined : Number(event.target.value),
              })
            }
          />
        </label>
      </div>
      <Readback>{describeWatch(model, watch)}</Readback>
      {read?.conditional && (
        <p className="preparation-note">
          This step sits on a branch and may not run. If it is skipped, nothing is observed.
        </p>
      )}
      <Problems problems={problems} id={`${id}-problems`} />
      <button type="button" onClick={onRemove}>
        Remove check {number}
      </button>
    </fieldset>
  )
}

function ValueSource({
  model,
  compared,
  value,
  onChange,
}: {
  model: SetupModel
  compared: OperationField | undefined
  value: ValueInput
  onChange: (value: ValueInput) => void
}) {
  const sources = model.bound.flatMap((item) =>
    (item.operation.results ?? [])
      .filter((result) => result.type === compared?.type)
      .map((result) => ({ item, result })),
  )
  return (
    <>
      <label>
        Compare with
        <select
          aria-label="Compare with"
          value={value.source}
          disabled={!compared}
          onChange={(event) =>
            onChange(
              event.target.value === 'result'
                ? { source: 'result', source_step_id: '', field: '' }
                : { source: 'literal', value: undefined },
            )
          }
        >
          <option value="literal">A value I enter</option>
          <option value="result">A result recorded by another step</option>
        </select>
      </label>
      {value.source === 'literal' ? (
        compared ? (
          <ScalarParameter
            field={{ ...compared, required: true, minimum: null, maximum: null, max_length: null }}
            value={value.value}
            context="Value that counts as met"
            requirement="Comparison value"
            clearable={false}
            onChange={(next) => onChange({ source: 'literal', value: next })}
          />
        ) : (
          <p className="preparation-note">Choose the evidence result first.</p>
        )
      ) : (
        <label>
          Recorded result
          <select
            aria-label="Recorded result"
            value={value.source_step_id ? `${value.source_step_id}/${value.field}` : ''}
            onChange={(event) => {
              const selected = sources.find(
                ({ item, result }) => `${item.step.id}/${result.name}` === event.target.value,
              )
              onChange(
                selected
                  ? {
                      source: 'result',
                      source_step_id: selected.item.step.id,
                      field: selected.result.name,
                    }
                  : { source: 'result', source_step_id: '', field: '' },
              )
            }}
          >
            <option value="">Choose a {compared?.type ?? ''} result</option>
            {sources.map(({ item, result }) => (
              <option
                key={`${item.step.id}/${result.name}`}
                value={`${item.step.id}/${result.name}`}
              >
                {stepName(item)} · {fieldName(result)}
              </option>
            ))}
          </select>
        </label>
      )}
    </>
  )
}

function GoalEditor({
  model,
  goal,
  problems,
  disabled,
  onChange,
}: {
  model: SetupModel
  goal: GoalInput
  problems: SetupProblem[]
  disabled: boolean
  onChange: (goal: GoalInput) => void
}) {
  const id = goalId(goal.objective_id)
  const objective = model.objectives.find((item) => item.id === goal.objective_id)
  const evidence = model.byId.get(goal.step_id)
  const compared = resultField(model, goal.step_id, goal.field)
  const clocks = model.bound.flatMap((item) =>
    (item.operation.results ?? [])
      .filter((result) => result.type === 'datetime')
      .map((result) => ({ item, result })),
  )
  const evidenceClocks = (evidence?.operation.results ?? []).filter(
    (result) => result.type === 'datetime',
  )
  return (
    <fieldset
      id={id}
      className="setup-check"
      disabled={disabled}
      aria-describedby={problems.length ? `${id}-problems` : undefined}
    >
      <legend>{objective?.title ?? 'Unavailable goal'}</legend>
      {objective && <p className="setup-criterion">{objective.criterion}</p>}
      <label className="check">
        <input
          type="checkbox"
          checked={goal.measured}
          onChange={(event) => onChange({ ...goal, measured: event.target.checked })}
        />
        Measure this goal from run evidence
      </label>
      {goal.measured && (
        <div className="preparation-fields">
          <label>
            Evidence step
            <select
              aria-label="Evidence step"
              value={goal.step_id}
              onChange={(event) =>
                onChange({
                  ...goal,
                  step_id: event.target.value,
                  field: '',
                  operator: 'eq',
                  value: { source: 'literal', value: undefined },
                  source_time_field: '',
                })
              }
            >
              <option value="">Choose a step</option>
              {model.bound.map((item) => (
                <option key={item.step.id} value={item.step.id}>
                  {stepName(item)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Evidence result
            <select
              aria-label="Evidence result"
              value={goal.field}
              disabled={!evidence}
              onChange={(event) =>
                onChange({
                  ...goal,
                  field: event.target.value,
                  operator: 'eq',
                  value: { source: 'literal', value: undefined },
                })
              }
            >
              <option value="">Choose a declared result</option>
              {(evidence?.operation.results ?? []).map((item) => (
                <option key={item.name} value={item.name}>
                  {fieldName(item)}
                </option>
              ))}
            </select>
          </label>
          <ComparatorField
            label="Met when the result"
            field={compared}
            value={goal.operator}
            onChange={(operator) => onChange({ ...goal, operator })}
          />
          <ValueSource
            model={model}
            compared={compared}
            value={goal.value}
            onChange={(value) => onChange({ ...goal, value })}
          />
          <label className="check full">
            <input
              type="checkbox"
              checked={goal.timed}
              onChange={(event) => onChange({ ...goal, timed: event.target.checked })}
            />
            It must happen within a time limit
          </label>
          {goal.timed && (
            <>
              <DurationField
                label="Time limit"
                seconds={goal.within_seconds}
                onChange={(within_seconds) => onChange({ ...goal, within_seconds })}
              />
              <label>
                Starting from
                <select
                  aria-label="Starting from"
                  value={goal.anchor_step_id ? `${goal.anchor_step_id}/${goal.anchor_field}` : ''}
                  onChange={(event) => {
                    const selected = clocks.find(
                      ({ item, result }) => `${item.step.id}/${result.name}` === event.target.value,
                    )
                    onChange({
                      ...goal,
                      anchor_step_id: selected?.item.step.id ?? '',
                      anchor_field: selected?.result.name ?? '',
                    })
                  }}
                >
                  <option value="">Choose a recorded time</option>
                  {clocks.map(({ item, result }) => (
                    <option
                      key={`${item.step.id}/${result.name}`}
                      value={`${item.step.id}/${result.name}`}
                    >
                      {stepName(item)} · {fieldName(result)}
                    </option>
                  ))}
                </select>
              </label>
              <fieldset className="full setup-inline">
                <legend>Which clock decides if it was on time</legend>
                <div className="setup-choices">
                  <label className="check">
                    <input
                      type="radio"
                      name={`${id}-clock`}
                      checked={goal.clock === 'observed'}
                      onChange={() => onChange({ ...goal, clock: 'observed' })}
                    />
                    When Game Theory observed it
                  </label>
                  <label className="check">
                    <input
                      type="radio"
                      name={`${id}-clock`}
                      checked={goal.clock === 'source'}
                      disabled={!evidenceClocks.length}
                      onChange={() => onChange({ ...goal, clock: 'source' })}
                    />
                    The time the system itself recorded
                  </label>
                </div>
                {goal.clock === 'source' && (
                  <label>
                    Recorded time
                    <select
                      aria-label="Recorded time"
                      value={goal.source_time_field}
                      onChange={(event) =>
                        onChange({ ...goal, source_time_field: event.target.value })
                      }
                    >
                      <option value="">Choose a time from the evidence step</option>
                      {evidenceClocks.map((item) => (
                        <option key={item.name} value={item.name}>
                          {fieldName(item)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </fieldset>
            </>
          )}
        </div>
      )}
      <Readback>{describeGoal(model, goal)}</Readback>
      {goal.measured && (evidence?.conditional || compared?.required === false) && (
        <p className="preparation-note">
          {evidence?.conditional
            ? 'This evidence step may not run. '
            : 'This result may be empty. '}
          Missing evidence keeps the goal indeterminate.
        </p>
      )}
      <Problems problems={problems} id={`${id}-problems`} />
    </fieldset>
  )
}

function UndoEditor({
  model,
  undo,
  problems,
  disabled,
  onChange,
}: {
  model: SetupModel
  undo: UndoInput
  problems: SetupProblem[]
  disabled: boolean
  onChange: (undo: UndoInput) => void
}) {
  const id = undoId(undo.step_id)
  const write = model.byId.get(undo.step_id)
  const option = model.recoveryOptions.find((item) => item.key === undo.operation_key)
  const fields = (option?.operation.parameters ?? []).filter(
    (field) => !isDispatcherOwned(option!.operation, field),
  )
  const owned = fields.filter((field) => {
    const input = undo.parameters[field.name]
    return input?.source === 'result' && input.source_step_id === undo.step_id
  })
  return (
    <fieldset
      id={id}
      className="setup-check"
      disabled={disabled}
      aria-describedby={problems.length ? `${id}-problems` : undefined}
    >
      <legend>{write ? stepName(write) : 'Unavailable change'}</legend>
      {write && <p className="setup-criterion">Registered guidance: {write.operation.recovery}</p>}
      <div className="setup-choices" role="radiogroup" aria-label={`Undo ${write?.step.label}`}>
        <label className="check">
          <input
            type="radio"
            name={`${id}-mode`}
            checked={undo.mode === 'manual'}
            onChange={() => onChange({ ...undo, mode: 'manual' })}
          />
          An operator handles it manually
        </label>
        <label className="check">
          <input
            type="radio"
            name={`${id}-mode`}
            checked={undo.mode === 'automatic'}
            disabled={!model.recoveryOptions.length}
            onChange={() => onChange({ ...undo, mode: 'automatic' })}
          />
          Undo automatically with a registered operation
        </label>
      </div>
      {undo.mode === 'automatic' && (
        <div className="preparation-fields">
          <label className="full">
            Undo operation
            <select
              aria-label="Undo operation"
              value={undo.operation_key}
              onChange={(event) => onChange(suggestedUndo(model, undo, event.target.value))}
            >
              <option value="">Choose a registered write</option>
              {model.recoveryOptions.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.operation.label} · {item.operation.key} @ {item.operation.version} ·{' '}
                  {item.configuration.connection_name}
                </option>
              ))}
            </select>
          </label>
          {option && (
            <>
              {fields.map((field) => (
                <UndoParameter
                  key={field.name}
                  model={model}
                  field={field}
                  opaqueVersion={
                    option.operation.invocation.kind === 'rest' && field.name === 'expected_version'
                  }
                  value={undo.parameters[field.name] ?? { source: 'literal', value: undefined }}
                  onChange={(value) =>
                    onChange({ ...undo, parameters: { ...undo.parameters, [field.name]: value } })
                  }
                />
              ))}
              {option.operation.invocation.kind === 'sql' &&
                option.operation.parameters?.some((field) => field.name === 'idempotency_key') && (
                  <p className="preparation-note full">
                    idempotency_key is supplied automatically for each undo attempt.
                  </p>
                )}
              <p className="preparation-note full">
                Inputs were matched to this change’s recorded results by name. Review every input
                before creating the run.
              </p>
              <label>
                Ownership check
                <select
                  aria-label="Ownership check"
                  value={undo.ownership_parameter}
                  onChange={(event) =>
                    onChange({ ...undo, ownership_parameter: event.target.value })
                  }
                >
                  <option value="">Choose an input from this change</option>
                  {owned.map((field) => (
                    <option key={field.name} value={field.name}>
                      {field.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Version check
                <select
                  aria-label="Version check"
                  value={undo.version_parameter}
                  onChange={(event) => onChange({ ...undo, version_parameter: event.target.value })}
                >
                  <option value="">Choose an input from this change</option>
                  {owned.map((field) => (
                    <option key={field.name} value={field.name}>
                      {field.name}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
        </div>
      )}
      <Readback>{describeUndo(model, undo)}</Readback>
      <Problems problems={problems} id={`${id}-problems`} />
    </fieldset>
  )
}

function UndoParameter({
  model,
  field,
  opaqueVersion,
  value,
  onChange,
}: {
  model: SetupModel
  field: OperationField
  opaqueVersion: boolean
  value: ValueInput
  onChange: (value: ValueInput) => void
}) {
  const sources = model.bound.flatMap((item) =>
    (item.operation.results ?? [])
      .filter((result) => result.type === field.type && result.required !== false)
      .map((result) => ({ item, result })),
  )
  return (
    <fieldset className="setup-inline">
      <legend>
        {field.name} · {field.type}
      </legend>
      <label>
        Value source
        <select
          aria-label="Value source"
          value={value.source}
          onChange={(event) =>
            onChange(
              event.target.value === 'result'
                ? { source: 'result', source_step_id: '', field: '' }
                : { source: 'literal', value: undefined },
            )
          }
        >
          <option value="literal">A value I enter</option>
          <option value="result">A result recorded during the run</option>
        </select>
      </label>
      {value.source === 'literal' ? (
        <ScalarParameter
          field={field}
          value={value.value}
          opaqueVersion={opaqueVersion}
          onChange={(next) => onChange({ source: 'literal', value: next })}
        />
      ) : (
        <label>
          Recorded result
          <select
            aria-label="Recorded result"
            value={value.source_step_id ? `${value.source_step_id}/${value.field}` : ''}
            onChange={(event) => {
              const selected = sources.find(
                ({ item, result }) => `${item.step.id}/${result.name}` === event.target.value,
              )
              onChange({
                source: 'result',
                source_step_id: selected?.item.step.id ?? '',
                field: selected?.result.name ?? '',
              })
            }}
          >
            <option value="">Choose a required {field.type} result</option>
            {sources.map(({ item, result }) => (
              <option
                key={`${item.step.id}/${result.name}`}
                value={`${item.step.id}/${result.name}`}
              >
                {stepName(item)} · {result.name}
              </option>
            ))}
          </select>
        </label>
      )}
    </fieldset>
  )
}
