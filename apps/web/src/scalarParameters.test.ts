import { describe, expect, it } from 'vitest'
import { scalarError, scalarFromInput } from './ScalarParameters'

describe('typed scalar parameters', () => {
  it('validates reserved REST expected_version without inventing or stripping the value', () => {
    const field = {
      name: 'expected_version',
      type: 'string' as const,
      required: true,
      max_length: 128,
    }
    for (const value of ['"v1"', '*', 'v 1', 'v\n1', '']) {
      expect(scalarError(field, value, true)).toBeDefined()
    }
    expect(scalarError(field, 'opaque/v1+token=', true)).toBeUndefined()
    expect(scalarFromInput(field, '"v1"', true)).toBe('"v1"')
    expect(scalarFromInput(field, '', true)).toBeUndefined()
    expect(scalarError(field, 'ordinary string with spaces', false)).toBeUndefined()
  })
  it('keeps omitted values unresolved and emits actual numeric and boolean values', () => {
    expect(scalarFromInput({ name: 'count', type: 'integer', required: true }, '')).toBeUndefined()
    expect(scalarFromInput({ name: 'count', type: 'integer', required: true }, '12')).toBe(12)
    expect(scalarFromInput({ name: 'enabled', type: 'boolean', required: true }, 'false')).toBe(
      false,
    )
    expect(scalarFromInput({ name: 'code', type: 'string', required: true }, '12')).toBe('12')
  })
  it('retains invalid text so it can be corrected or exported, but never validates it for saving', () => {
    const field = {
      name: 'count',
      type: 'integer' as const,
      required: true,
      minimum: 0,
      maximum: 20,
    }
    const invalid = scalarFromInput(field, '1e999')
    expect(invalid).toBe('1e999')
    expect(scalarError(field, invalid)).toBeDefined()
    expect(scalarError(field, 1.5)).toBeDefined()
    expect(scalarError(field, 21)).toBeDefined()
    expect(scalarError(field, 10)).toBeUndefined()
  })
  it('does not coerce boolean or numeric strings from a saved contract', () => {
    expect(scalarError({ name: 'count', type: 'number', required: true }, '10')).toBeDefined()
    expect(scalarError({ name: 'enabled', type: 'boolean', required: true }, 'false')).toBeDefined()
  })
  it('requires UUIDs and timezone-aware dates when they are supplied', () => {
    expect(scalarError({ name: 'id', type: 'uuid', required: true }, 'unresolved')).toBeDefined()
    expect(
      scalarError({ name: 'at', type: 'datetime', required: true }, '2030-01-01T12:00'),
    ).toBeDefined()
    expect(
      scalarError({ name: 'at', type: 'datetime', required: true }, '2030-01-01T12:00:00Z'),
    ).toBeUndefined()
  })
})
