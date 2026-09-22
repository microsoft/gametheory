import { useId } from 'react'
import type { OperationField } from './Catalog'
import type { components } from './api.generated'

export type ScalarValue = Exclude<
  NonNullable<NonNullable<components['schemas']['PreparationStep']['parameters']>[string]>,
  object
>

export function scalarError(
  field: OperationField,
  value: ScalarValue | undefined,
  opaqueVersion = false,
): string | undefined {
  if (value === undefined) return undefined
  if (field.type === 'boolean' && typeof value !== 'boolean') return 'Supply a boolean, not text.'
  if (field.type === 'integer' && (typeof value !== 'number' || !Number.isSafeInteger(value)))
    return 'Supply a safe, whole number.'
  if (field.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value)))
    return 'Supply a finite number.'
  if (['string', 'uuid', 'datetime'].includes(field.type) && typeof value !== 'string')
    return `Supply ${field.type} text.`
  if (
    field.type === 'uuid' &&
    typeof value === 'string' &&
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
  )
    return 'Supply a complete UUID, or clear it to mark this input unresolved.'
  if (
    field.type === 'datetime' &&
    typeof value === 'string' &&
    (!/^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ||
      !Number.isFinite(Date.parse(value)))
  )
    return 'Supply an ISO date and time with an explicit timezone.'
  if (typeof value === 'number') {
    if (field.minimum != null && value < field.minimum) return `Minimum: ${field.minimum}.`
    if (field.maximum != null && value > field.maximum) return `Maximum: ${field.maximum}.`
  }
  if (typeof value === 'string') {
    if (opaqueVersion && (!value || value === '*' || /[^\x21-\x7e]|"/.test(value)))
      return 'Use a non-wildcard, printable ASCII version without ETag quotes or whitespace, or clear it to leave the input unresolved.'
    if (value.length > 4000) return 'Use no more than 4,000 characters.'
    if (field.max_length != null && value.length > field.max_length)
      return `Use no more than ${field.max_length} characters.`
    if (field.choices?.length && !field.choices.includes(value)) return 'Choose a registered value.'
  }
  return undefined
}

export function scalarFromInput(
  field: OperationField,
  raw: string,
  opaqueVersion = false,
): ScalarValue | undefined {
  if (opaqueVersion && raw === '') return undefined
  if (field.type === 'boolean')
    return raw === '' ? undefined : raw === 'true' ? true : raw === 'false' ? false : raw
  if (field.type === 'integer' || field.type === 'number') {
    if (raw === '') return undefined
    if (!/^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw)) return raw
    const number = Number(raw)
    return Number.isFinite(number) ? number : raw
  }
  return raw === '' && field.type !== 'string' ? undefined : raw
}

export function ScalarParameter({
  field,
  value,
  disabled,
  onChange,
  context = '',
  opaqueVersion = false,
}: {
  field: OperationField
  value: ScalarValue | undefined
  disabled?: boolean
  onChange: (value: ScalarValue | undefined) => void
  context?: string
  opaqueVersion?: boolean
}) {
  const id = useId()
  const error = scalarError(field, value, opaqueVersion)
  const label = `${context ? `${context} · ` : ''}${field.name}`
  const attributes = {
    id,
    disabled,
    'aria-invalid': error ? true : undefined,
    'aria-describedby': `${id}-hint${error ? ` ${id}-error` : ''}`,
  }
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      {field.type === 'boolean' ? (
        <select
          {...attributes}
          value={value === undefined ? '' : String(value)}
          onChange={(event) => onChange(scalarFromInput(field, event.target.value, opaqueVersion))}
        >
          <option value="">Not supplied</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : field.choices?.length ? (
        <select
          {...attributes}
          value={value === undefined ? '' : String(field.choices.indexOf(String(value)))}
          onChange={(event) =>
            onChange(
              event.target.value === '' ? undefined : field.choices?.[Number(event.target.value)],
            )
          }
        >
          <option value="">Not supplied</option>
          {field.choices.map((choice, index) => (
            <option key={index} value={index}>
              {choice || '(empty string)'}
            </option>
          ))}
        </select>
      ) : (
        <input
          {...attributes}
          type="text"
          inputMode={
            field.type === 'integer' ? 'numeric' : field.type === 'number' ? 'decimal' : 'text'
          }
          value={value === undefined ? '' : String(value)}
          onChange={(event) => onChange(scalarFromInput(field, event.target.value, opaqueVersion))}
          placeholder="Not supplied"
        />
      )}
      <p id={`${id}-hint`} className="preparation-note">
        {field.type} · {field.required !== false ? 'Required by operation' : 'Optional'}
        {field.minimum != null && ` · Minimum ${field.minimum}`}
        {field.maximum != null && ` · Maximum ${field.maximum}`}
        {field.max_length != null && ` · Up to ${field.max_length} characters`}
        {value === undefined && ' · Unresolved'}
        {opaqueVersion &&
          ' · Bare version only; a future adapter would add HTTP ETag quotes. No header is sent here.'}
      </p>
      {error && (
        <p id={`${id}-error`} className="error-text" role="alert">
          {error}
        </p>
      )}
      {value !== undefined && !disabled && (
        <button type="button" onClick={() => onChange(undefined)} aria-label={`Clear ${label}`}>
          Clear value
        </button>
      )}
    </div>
  )
}
