import { describe, expect, it } from 'vitest'
import { validateOperationCatalog } from './catalogValidation'

const operation = {
  key: 'ticket.read',
  version: '1',
  label: 'Read a service ticket',
  effect: 'read',
  invocation: { kind: 'rest', method: 'GET', path: '/tickets/{ticket_id}' },
  parameters: [{ name: 'ticket_id', type: 'uuid', required: true }],
  results: [{ name: 'priority', type: 'integer' }],
  recovery: 'Read-only; no target changes.',
}
const catalog = {
  schema_version: 'operation-catalog/v1',
  name: 'Service tickets',
  operations: [operation],
}

describe('operation catalog inspection', () => {
  it.each(['idempotency_key', 'if_match', 'IdempotencyKey'])(
    'rejects caller-controlled REST transport slot %s',
    (name) => {
      expect(() =>
        validateOperationCatalog(
          {
            ...catalog,
            operations: [
              {
                ...operation,
                parameters: [...operation.parameters, { name, type: 'string', required: true }],
              },
            ],
          },
          'rest',
        ),
      ).toThrow(/transport headers/)
    },
  )
  it('requires an explicitly bounded expected_version string outside the path', () => {
    const bounded = { name: 'expected_version', type: 'string', required: true, max_length: 128 }
    const withField = (field: unknown, path = operation.invocation.path) => ({
      ...catalog,
      operations: [
        {
          ...operation,
          parameters: [...operation.parameters, field],
          invocation: { ...operation.invocation, path },
        },
      ],
    })
    expect(() => validateOperationCatalog(withField(bounded), 'rest')).not.toThrow()
    expect(() =>
      validateOperationCatalog(withField({ ...bounded, max_length: undefined }), 'rest'),
    ).toThrow(/explicit max_length/)
    expect(() =>
      validateOperationCatalog(withField(bounded, '/tickets/{expected_version}'), 'rest'),
    ).toThrow(/never a path/)
    expect(() =>
      validateOperationCatalog(withField({ ...bounded, choices: ['"quoted"'] }), 'rest'),
    ).toThrow(/bare opaque/)
  })
  it('accepts restricted externally supplied descriptions without contacting them', () => {
    expect(() => validateOperationCatalog(catalog, 'rest')).not.toThrow()
  })
  it.each([
    { ...catalog, $ref: 'https://example.test/schema' },
    { ...catalog, operations: [operation, operation] },
    {
      ...catalog,
      operations: [{ ...operation, parameters: [{ name: 'ticket_id', type: 'object' }] }],
    },
    { ...catalog, operations: [{ ...operation, credentials: 'not accepted' }] },
  ])('rejects unsupported fields, duplicate versions, and non-scalar parameters', (input) => {
    expect(() => validateOperationCatalog(input, 'rest')).toThrow()
  })
  it.each([
    'https://example.test/tickets',
    '//example.test/tickets',
    '/tickets/../private',
    '/tickets/%2e%2e/private',
    '/tickets?token=value',
    '/tickets/{undeclared}',
  ])('rejects an unrestricted HTTP path %s', (path) => {
    expect(() =>
      validateOperationCatalog(
        {
          ...catalog,
          operations: [{ ...operation, invocation: { kind: 'rest', method: 'GET', path } }],
        },
        'rest',
      ),
    ).toThrow()
  })
  it('rejects SQL text and integration mismatches', () => {
    expect(() => validateOperationCatalog(catalog, 'sql')).toThrow(/integration/)
    expect(() =>
      validateOperationCatalog(
        {
          ...catalog,
          operations: [
            { ...operation, invocation: { kind: 'sql', procedure: 'SELECT * FROM users' } },
          ],
        },
        'sql',
      ),
    ).toThrow(/raw SQL/)
  })
  it('rejects operation-selected notification recipients', () => {
    expect(() =>
      validateOperationCatalog(
        {
          ...catalog,
          operations: [
            {
              ...operation,
              effect: 'notify',
              invocation: { kind: 'graph', template_key: 'ticket-summary' },
              parameters: [{ name: 'recipients', type: 'string' }],
            },
          ],
        },
        'graph',
      ),
    ).toThrow(/cannot choose/)
  })
})
