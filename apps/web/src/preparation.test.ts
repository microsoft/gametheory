import { describe, expect, it } from 'vitest'
import {
  draftErrors,
  parameterValue,
  type BoardDraft,
  type Configuration,
  type PreparationStep,
} from './preparation'

const configuration: Configuration = {
  id: 'configuration',
  workspace_id: 'workspace',
  connection_id: 'connection',
  version: 1,
  connection_name: 'Record service',
  connection_kind: 'rest',
  environment_id: 'environment',
  environment_name: 'Nonproduction',
  digest: 'a'.repeat(64),
  created_by: 'administrator',
  created_at: '2026-01-01T00:00:00Z',
  execution_authorized: false,
  content: {
    schema_version: 'connection-configuration/v1',
    classification: 'unknown',
    resource_id: '',
    endpoint: '',
    database: '',
    identity_ref: '',
    catalog: {
      schema_version: 'operation-catalog/v1',
      name: 'Record service',
      operations: [
        {
          key: 'record.create',
          version: '1',
          label: 'Create record',
          effect: 'write',
          invocation: { kind: 'rest', method: 'POST', path: '/records' },
          parameters: [],
          results: [{ name: 'record_id', type: 'uuid', required: true }],
          recovery: 'External ownership and version checks required.',
        },
        {
          key: 'record.read',
          version: '1',
          label: 'Read record',
          effect: 'read',
          invocation: { kind: 'rest', method: 'GET', path: '/records/{record_id}' },
          parameters: [{ name: 'record_id', type: 'uuid', required: true }],
          results: [{ name: 'count', type: 'integer', required: true }],
          recovery: 'Read-only.',
        },
      ],
    },
  },
}
const source: PreparationStep = {
  id: 'source',
  kind: 'operation',
  label: 'Create record',
  depends_on: [],
  parameters: {},
  binding: {
    configuration_id: configuration.id,
    operation_key: 'record.create',
    operation_version: '1',
  },
}
const read: PreparationStep = {
  id: 'read',
  kind: 'operation',
  label: 'Read record',
  depends_on: [source.id],
  binding: {
    configuration_id: configuration.id,
    operation_key: 'record.read',
    operation_version: '1',
  },
  parameters: { record_id: { source_step_id: source.id, field: 'record_id' } },
}
function draft(steps: PreparationStep[]): BoardDraft {
  return {
    schema_version: 'exercise-preparation-draft/v1',
    name: 'Generic preparation',
    steps,
    notification_budget: 0,
    recovery: '',
    window: null,
  }
}

describe('preparation editing constraints', () => {
  it('never mistakes inherited object properties for supplied parameter values', () => {
    expect(parameterValue(source, 'constructor')).toBeUndefined()
    expect(parameterValue(source, '__proto__')).toBeUndefined()
    expect(
      parameterValue({ ...source, parameters: { ['__proto__']: 'explicit value' } }, '__proto__'),
    ).toBe('explicit value')
  })
  it('allows explicitly unresolved inputs without fabricating values', () => {
    expect(draftErrors(draft([{ ...source, binding: null }]), [configuration])).toEqual([])
  })
  it('accepts a typed prior result instead of inventing a future record identifier', () => {
    expect(draftErrors(draft([source, read]), [configuration])).toEqual([])
  })
  it('requires referenced results to be guaranteed predecessors, not independent sibling roots', () => {
    const parallel = { ...source, id: 'parallel', label: 'Parallel record' }
    const consumer = {
      ...read,
      parameters: { record_id: { source_step_id: parallel.id, field: 'record_id' } },
    }
    expect(draftErrors(draft([source, parallel, consumer]), [configuration]).join(' ')).toContain(
      'guaranteed predecessor',
    )
    expect(
      draftErrors(
        draft([source, parallel, { ...consumer, depends_on: [source.id, parallel.id] }]),
        [configuration],
      ),
    ).toEqual([])
  })
  it('rejects a prior-result dependency on a mutually exclusive branch sibling', () => {
    const left = { ...source, id: 'left', label: 'Left branch producer' }
    const right = {
      ...read,
      id: 'right',
      label: 'Right branch consumer',
      depends_on: [left.id],
      parameters: { record_id: { source_step_id: left.id, field: 'record_id' } },
    }
    const branch: PreparationStep = {
      id: 'branch',
      label: 'Branch',
      kind: 'condition',
      depends_on: [read.id],
      condition: {
        source_step_id: read.id,
        result_field: 'count',
        operator: 'gt',
        value: 0,
        if_true: [left.id],
        if_false: [right.id],
      },
    }
    expect(
      draftErrors(draft([source, read, branch, left, right]), [configuration]).join(' '),
    ).toContain('mutually exclusive')
  })
  it('retains only common predecessors at an alternative branch merge', () => {
    const left = { ...source, id: 'left', label: 'Left producer' }
    const merge = {
      ...read,
      id: 'merge',
      label: 'Merged consumer',
      depends_on: [],
      parameters: { record_id: { source_step_id: left.id, field: 'record_id' } },
    }
    const condition = (
      id: string,
      depends_on: string[],
      yes: string[],
      no: string[] = [],
    ): PreparationStep => ({
      id,
      label: id,
      kind: 'condition',
      depends_on,
      condition: {
        source_step_id: read.id,
        result_field: 'count',
        operator: 'gt',
        value: 0,
        if_true: yes,
        if_false: no,
      },
    })
    const branch = condition('branch', [read.id], [left.id], ['right-gate'])
    const leftGate = condition('left-gate', [left.id], [merge.id])
    const rightGate = condition('right-gate', [], [merge.id])
    const steps = [source, read, branch, left, leftGate, rightGate, merge]
    expect(draftErrors(draft(steps), [configuration]).join(' ')).toContain('guaranteed predecessor')
    expect(
      draftErrors(
        draft([
          ...steps.slice(0, -1),
          {
            ...merge,
            parameters: { record_id: { source_step_id: source.id, field: 'record_id' } },
          },
        ]),
        [configuration],
      ),
    ).toEqual([])
  })
  it('rejects references to unavailable or mismatched declared outputs', () => {
    const invalid = {
      ...read,
      parameters: { record_id: { source_step_id: source.id, field: 'missing' } },
    }
    expect(draftErrors(draft([source, invalid]), [configuration]).join(' ')).toContain(
      'declared prior operation result',
    )
    const wrongType = {
      ...read,
      parameters: { record_id: { source_step_id: 'another', field: 'count' } },
    }
    expect(
      draftErrors(draft([source, { ...read, id: 'another' }, wrongType]), [configuration]).join(
        ' ',
      ),
    ).toContain('uuid type')
  })
  it('rejects optional outputs for both parameter references and conditions', () => {
    const optional: Configuration = {
      ...configuration,
      content: {
        ...configuration.content,
        catalog: {
          ...configuration.content.catalog,
          operations: configuration.content.catalog.operations.map((operation) => ({
            ...operation,
            results: operation.results.map((field) => ({ ...field, required: false })),
          })),
        },
      },
    }
    const wait: PreparationStep = { id: 'wait', kind: 'wait', label: 'Wait', wait_seconds: 1 }
    const condition: PreparationStep = {
      id: 'condition',
      kind: 'condition',
      label: 'Compare count',
      depends_on: [read.id],
      condition: {
        source_step_id: read.id,
        result_field: 'count',
        operator: 'eq',
        value: 1,
        if_true: [wait.id],
        if_false: [],
      },
    }
    const errors = draftErrors(draft([source, read, condition, wait]), [optional]).join(' ')
    expect(errors).toContain('optional outputs cannot supply a result reference')
    expect(errors).toContain('not an optional output')
  })
  it('detects cycles across both dependencies and conditional branches', () => {
    const condition: PreparationStep = {
      id: 'branch',
      label: 'Compare result',
      kind: 'condition',
      depends_on: [read.id],
      condition: {
        source_step_id: read.id,
        result_field: 'count',
        operator: 'gt',
        value: 2,
        if_true: [source.id],
        if_false: [],
      },
    }
    expect(draftErrors(draft([source, read, condition]), [configuration]).join(' ')).toContain(
      'cycle',
    )
  })
  it('rejects shared branch targets, invalid waits, and windows over seven days', () => {
    const wait: PreparationStep = { id: 'wait', label: 'Wait', kind: 'wait', wait_seconds: 86401 }
    const condition: PreparationStep = {
      id: 'branch',
      label: 'Compare result',
      kind: 'condition',
      depends_on: [read.id],
      condition: {
        source_step_id: read.id,
        result_field: 'count',
        operator: 'gt',
        value: 2,
        if_true: [wait.id],
        if_false: [wait.id],
      },
    }
    const input = {
      ...draft([source, read, wait, condition]),
      window: { starts_at: '2030-01-01T00:00:00Z', ends_at: '2030-01-09T00:00:00Z' },
    }
    const errors = draftErrors(input, [configuration]).join(' ')
    expect(errors).toContain('distinct')
    expect(errors).toContain('bounded wait')
    expect(errors).toContain('seven days')
  })
})
