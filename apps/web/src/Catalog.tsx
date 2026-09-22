import type { components } from './api.generated'
import { validateOperationCatalog } from './catalogValidation'

export type OperationCatalog = components['schemas']['OperationCatalog']
export type RegisteredOperation = NonNullable<OperationCatalog['operations']>[number]
export type OperationField = NonNullable<RegisteredOperation['parameters']>[number]

function assertCatalog(value: unknown, kind: string): asserts value is OperationCatalog {
  validateOperationCatalog(value, kind)
}

export function readOperationCatalog(value: unknown, kind: string): OperationCatalog {
  assertCatalog(value, kind)
  return {
    ...value,
    operations: value.operations.map((operation) => ({
      ...operation,
      parameters: operation.parameters.map((field) => ({
        ...field,
        required: field.required ?? true,
      })),
      results: operation.results.map((field) => ({ ...field, required: field.required ?? true })),
    })),
  }
}

export function invocationLabel(operation: RegisteredOperation) {
  const invocation = operation.invocation
  if (invocation.kind === 'sql') return invocation.procedure
  if (invocation.kind === 'rest') return `${invocation.method} ${invocation.path}`
  return `Fixed template: ${invocation.template_key}`
}

export function CatalogInspection({ catalog }: { catalog: OperationCatalog }) {
  return (
    <section aria-label="Catalog inspection">
      <h3>{catalog.name}</h3>
      <p className="preparation-note">
        {catalog.schema_version} · {(catalog.operations ?? []).length} registered descriptions. This
        review does not verify connectivity, permissions, target contents, or delivery.
      </p>
      <ul className="preparation-list">
        {(catalog.operations ?? []).map((operation) => (
          <li key={`${operation.key}/${operation.version}`}>
            <h3>{operation.label}</h3>
            <div className="toolbar">
              <code>{operation.key}</code>
              <span className="pill">Version {operation.version}</span>
              <span className="pill">Proposed {operation.effect}</span>
            </div>
            <dl className="preparation-meta">
              <dt>Allowlisted invocation</dt>
              <dd>
                <code>{invocationLabel(operation)}</code>
              </dd>
              <dt>Recovery description</dt>
              <dd>{operation.recovery || 'Not supplied'}</dd>
            </dl>
            <FieldInspection fields={operation.parameters ?? []} label="Parameters" />
            <FieldInspection fields={operation.results ?? []} label="Declared results" />
            {operation.invocation.kind === 'rest' &&
              operation.parameters?.some((field) => field.name === 'expected_version') && (
                <p className="preparation-note">
                  Reserved expected_version is the opaque version value without ETag quotes. A
                  future adapter would map it to strong If-Match, not the body or query. Write
                  idempotency keys would be assigned by a future dispatcher. No request is
                  dispatched here.
                </p>
              )}
          </li>
        ))}
      </ul>
      {!catalog.operations?.length && (
        <p className="notice">
          This catalog has no operations. No preparation step can bind to it.
        </p>
      )}
      <details>
        <summary>Inspect complete catalog JSON</summary>
        <pre>{JSON.stringify(catalog, null, 2)}</pre>
      </details>
    </section>
  )
}

function FieldInspection({ fields, label }: { fields: OperationField[]; label: string }) {
  if (!fields.length) return <p className="preparation-note">{label}: none declared.</p>
  return (
    <details>
      <summary>
        {label} ({fields.length})
      </summary>
      <div className="preparation-table-wrap">
        <table className="preparation-table">
          <caption className="sr-only">{label}</caption>
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Type</th>
              <th scope="col">Constraints</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((field) => (
              <tr key={field.name}>
                <th scope="row">
                  <code>{field.name}</code>
                </th>
                <td>
                  {field.type} · {field.required !== false ? 'Required' : 'Optional'}
                </td>
                <td>
                  {[
                    field.minimum != null ? `Minimum ${field.minimum}` : '',
                    field.maximum != null ? `Maximum ${field.maximum}` : '',
                    field.max_length != null ? `Up to ${field.max_length} characters` : '',
                    field.choices?.length ? `Choices: ${field.choices.join(', ')}` : '',
                  ]
                    .filter(Boolean)
                    .join('; ') || 'No additional constraints'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
