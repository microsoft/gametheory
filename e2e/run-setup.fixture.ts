import type { Page, Request } from '@playwright/test'
import type { components } from '../apps/web/src/api.generated'
import type { BoardView, Configuration, Preview } from '../apps/web/src/preparation'

type Schemas = components['schemas']
export const runIds = {
  workspace: '31000000-0000-4000-8000-000000000001',
  board: '31000000-0000-4000-8000-000000000002',
  scenario: '31000000-0000-4000-8000-000000000003',
  sqlConnection: '31000000-0000-4000-8000-000000000004',
  restConnection: '31000000-0000-4000-8000-000000000005',
  sqlConfiguration: '31000000-0000-4000-8000-000000000006',
  restConfiguration: '31000000-0000-4000-8000-000000000007',
  environment: '31000000-0000-4000-8000-000000000008',
  operator: '31000000-0000-4000-8000-000000000009',
  raise: '31000000-0000-4000-8000-00000000000a',
  readOccupancy: '31000000-0000-4000-8000-00000000000b',
  openTicket: '31000000-0000-4000-8000-00000000000c',
  readTicket: '31000000-0000-4000-8000-00000000000d',
  breach: '31000000-0000-4000-8000-00000000000e',
  acknowledged: '31000000-0000-4000-8000-00000000000f',
  preview: '31000000-0000-4000-8000-000000000010',
  run: '31000000-0000-4000-8000-000000000011',
  shelter: '31000000-0000-4000-8000-000000000012',
}
const receipt: Schemas['OperationField'][] = [
  { name: 'outcome', type: 'string', required: true },
  { name: 'durable_event_id', type: 'uuid', required: true },
  { name: 'committed_at', type: 'datetime', required: true },
]
const sqlCatalog: Schemas['OperationCatalog'] = {
  schema_version: 'operation-catalog/v1',
  name: 'Capacity records — test fixture',
  operations: [
    {
      key: 'capacity.update',
      version: '2',
      label: 'Set shelter occupancy',
      effect: 'write',
      invocation: { kind: 'sql', procedure: 'fixture.UpdateOccupancy' },
      parameters: [
        { name: 'shelter_id', type: 'uuid', required: true },
        { name: 'occupancy', type: 'integer', required: true, minimum: 0, maximum: 500 },
        { name: 'idempotency_key', type: 'string', required: true, max_length: 128 },
      ],
      results: [
        { name: 'occupancy_percent', type: 'number', required: true },
        { name: 'record_version', type: 'string', required: true, max_length: 128 },
        ...receipt,
      ],
      recovery: 'Restore the seeded occupancy only if the version still matches.',
    },
    {
      key: 'capacity.read',
      version: '2',
      label: 'Read shelter occupancy',
      effect: 'read',
      invocation: { kind: 'sql', procedure: 'fixture.ReadOccupancy' },
      parameters: [{ name: 'shelter_id', type: 'uuid', required: true }],
      results: [
        { name: 'occupancy_percent', type: 'number', required: true },
        { name: 'committed_at', type: 'datetime', required: false },
      ],
      recovery: 'Read-only; no target mutation.',
    },
  ],
}
const restCatalog: Schemas['OperationCatalog'] = {
  schema_version: 'operation-catalog/v1',
  name: 'Resource tickets — test fixture',
  operations: [
    {
      key: 'ticket.create',
      version: '1',
      label: 'Open a resource ticket',
      effect: 'write',
      invocation: { kind: 'rest', method: 'POST', path: '/tickets' },
      parameters: [{ name: 'title', type: 'string', required: true, max_length: 80 }],
      results: [
        { name: 'record_id', type: 'uuid', required: true },
        { name: 'run_id', type: 'uuid', required: true },
        { name: 'record_version', type: 'string', required: true, max_length: 128 },
        { name: 'created_at', type: 'datetime', required: true },
        ...receipt,
      ],
      recovery: 'Close the ticket only while it is unchanged and owned by this run.',
    },
    {
      key: 'ticket.read',
      version: '1',
      label: 'Read a resource ticket',
      effect: 'read',
      invocation: { kind: 'rest', method: 'GET', path: '/tickets/{record_id}' },
      parameters: [{ name: 'record_id', type: 'uuid', required: true }],
      results: [
        { name: 'acknowledged', type: 'boolean', required: true },
        { name: 'acknowledged_at', type: 'datetime', required: false },
        { name: 'record_version', type: 'string', required: true, max_length: 128 },
      ],
      recovery: 'Read-only; no target mutation.',
    },
    {
      key: 'ticket.close',
      version: '1',
      label: 'Close an exercise ticket',
      effect: 'write',
      invocation: { kind: 'rest', method: 'POST', path: '/tickets/{record_id}/close' },
      parameters: [
        { name: 'record_id', type: 'uuid', required: true },
        { name: 'run_id', type: 'uuid', required: true },
        { name: 'expected_version', type: 'string', required: true, max_length: 128 },
      ],
      results: [
        { name: 'record_version', type: 'string', required: true, max_length: 128 },
        ...receipt,
      ],
      recovery: 'Closing is itself the recovery; human edits are preserved.',
    },
  ],
}
const date = '2026-09-22T12:00:00Z'

function configuration(
  id: string,
  connection: string,
  kind: 'sql' | 'rest',
  name: string,
  catalog: Schemas['OperationCatalog'],
): Configuration {
  return {
    id,
    workspace_id: runIds.workspace,
    connection_id: connection,
    version: 1,
    content: {
      schema_version: 'connection-configuration/v1',
      classification: 'nonproduction',
      resource_id: `fixture/${kind}`,
      endpoint: kind === 'sql' ? 'sql.fixture.invalid' : 'https://tickets.fixture.invalid',
      database: kind === 'sql' ? 'exercise_fixture' : '',
      identity_ref: `fixture/${kind}/executor`,
      catalog,
      notification: null,
    },
    digest: (kind === 'sql' ? 'c' : 'd').repeat(64),
    connection_kind: kind,
    connection_name: name,
    environment_id: runIds.environment,
    environment_name: 'Training lab',
    template_asset: null,
    created_by: runIds.operator,
    created_at: date,
    withdrawn_at: null,
    withdrawn_by: null,
    execution_authorized: false,
  }
}

export function runSetupBoard(options: { idempotencyLiteral?: boolean } = {}) {
  const sql = configuration(
    runIds.sqlConfiguration,
    runIds.sqlConnection,
    'sql',
    'Capacity database',
    sqlCatalog,
  )
  const rest = configuration(
    runIds.restConfiguration,
    runIds.restConnection,
    'rest',
    'Resource ticket API',
    restCatalog,
  )
  const now = Date.now()
  const draft: Schemas['BoardDraft'] = {
    schema_version: 'exercise-preparation-draft/v1',
    name: 'Shelter surge drill',
    notification_budget: 0,
    recovery: 'Close exercise tickets and restore seeded occupancy where unchanged.',
    window: {
      starts_at: new Date(now - 60000).toISOString(),
      ends_at: new Date(now + 2 * 3600000).toISOString(),
    },
    steps: [
      {
        id: runIds.raise,
        label: 'Raise occupancy',
        kind: 'operation',
        depends_on: [],
        binding: {
          configuration_id: sql.id,
          operation_key: 'capacity.update',
          operation_version: '2',
        },
        parameters: {
          shelter_id: runIds.shelter,
          occupancy: 108,
          ...(options.idempotencyLiteral ? { idempotency_key: 'typed-by-hand' } : {}),
        },
      },
      {
        id: runIds.readOccupancy,
        label: 'Read occupancy',
        kind: 'operation',
        depends_on: [runIds.raise],
        binding: {
          configuration_id: sql.id,
          operation_key: 'capacity.read',
          operation_version: '2',
        },
        parameters: { shelter_id: runIds.shelter },
      },
      {
        id: runIds.openTicket,
        label: 'Open resource ticket',
        kind: 'operation',
        depends_on: [runIds.readOccupancy],
        binding: {
          configuration_id: rest.id,
          operation_key: 'ticket.create',
          operation_version: '1',
        },
        parameters: { title: 'EXERCISE ONLY — cots needed' },
      },
      {
        id: runIds.readTicket,
        label: 'Read ticket',
        kind: 'operation',
        depends_on: [runIds.openTicket],
        binding: {
          configuration_id: rest.id,
          operation_key: 'ticket.read',
          operation_version: '1',
        },
        parameters: { record_id: { source_step_id: runIds.openTicket, field: 'record_id' } },
      },
    ],
  }
  const scenario: Schemas['ScenarioPin'] = {
    scenario_id: runIds.scenario,
    revision_version: 1,
    content: {
      schema_version: 1,
      title: 'Shelter surge',
      document: { type: 'doc', content: [{ type: 'paragraph' }] },
      objectives: [
        {
          id: runIds.breach,
          title: 'Breach identified quickly',
          criterion: 'Occupancy above 85% is observed within 2 minutes of the injected change.',
        },
        {
          id: runIds.acknowledged,
          title: 'Ticket acknowledged',
          criterion: 'Someone acknowledges the ticket within 10 minutes of it opening.',
        },
      ],
      nodes: [],
      edges: [],
      asset_ids: [],
    },
  }
  const snapshot = (item: Configuration) => {
    const { withdrawn_at: _at, withdrawn_by: _by, ...rest } = item
    return rest
  }
  const preview: Preview = {
    id: runIds.preview,
    board_id: runIds.board,
    board_version: 3,
    sequence: 1,
    digest: 'e'.repeat(64),
    manifest: {
      schema_version: 'exercise-preparation/v1',
      board_id: runIds.board,
      workspace_id: runIds.workspace,
      board_version: 3,
      draft,
      scenario,
      assets: [],
      configurations: [snapshot(sql), snapshot(rest)],
      execution_authorized: false,
    },
    findings: [],
    created_by: runIds.operator,
    created_at: date,
    is_current: true,
    execution_authorized: false,
    execution_eligible: false,
  }
  const board: BoardView = {
    id: runIds.board,
    workspace_id: runIds.workspace,
    name: draft.name,
    version: 3,
    scenario_id: runIds.scenario,
    revision_version: 1,
    created_by: runIds.operator,
    created_at: date,
    updated_at: date,
    preparation_status: 'previewed',
    approval_status: 'none',
    execution_authorized: false,
    draft,
    scenario,
    assets: [],
    contributors: [runIds.operator],
    latest_preview: preview,
    current_approval: null,
    can_edit: true,
    can_approve: false,
    approval_blockers: ['explicit_approver_grant_required'],
  }
  return { board, preview, configurations: [sql, rest] }
}

export function preflightView(
  body: Schemas['RunCreate'],
  preview: Preview,
  blockers: Schemas['RunBlocker'][] = [],
): Schemas['RunPreflightView'] {
  const window = preview.manifest.draft.window!
  return {
    valid: true,
    checked_at: new Date().toISOString(),
    trigger: body.trigger,
    window_starts_at: window.starts_at,
    window_ends_at: window.ends_at,
    max_operations: 1000,
    planned_attempts:
      4 + (body.observations?.[0]?.max_samples ?? 1) - 1 + (body.recovery?.length ?? 0),
    approval_required: false,
    environments: [
      {
        environment_id: runIds.environment,
        name: 'Training lab',
        classification: 'nonproduction',
        execution_enabled: true,
        approval_required: false,
        version: 4,
        updated_by: runIds.operator,
        updated_at: date,
      },
    ],
    targets: preview.manifest.configurations.map((item) => ({
      configuration_id: item.id,
      connection_name: item.connection_name,
      connection_kind: item.connection_kind,
      environment_id: item.environment_id,
      environment_name: item.environment_name,
      classification: item.content.classification,
      authority: {
        configuration_id: item.id,
        resource_id: item.content.resource_id,
        endpoint: item.content.endpoint,
        database: item.content.database,
        identity_ref: item.content.identity_ref,
        client_id: '31000000-0000-4000-8000-0000000000aa',
        token_scope: item.connection_kind === 'rest' ? 'api://fixture/.default' : '',
        operation_digests: ['f'.repeat(64)],
        replayable_operations: [],
      },
      readiness:
        item.connection_kind === 'rest'
          ? null
          : {
              id: '31000000-0000-4000-8000-0000000000bb',
              configuration_id: item.id,
              checked_at: date,
              expires_at: new Date(Date.parse(window.ends_at) + 3600000).toISOString(),
              evidence_reference: 'fixture:operator-attested-only',
              operator: 'sql:fixture-operator',
            },
      latest_readiness: null,
    })),
    recovery: [
      {
        step_id: runIds.raise,
        label: 'Raise occupancy',
        mode: body.recovery?.some((item) => item.step_id === runIds.raise) ? 'automatic' : 'manual',
      },
      {
        step_id: runIds.openTicket,
        label: 'Open resource ticket',
        mode: body.recovery?.some((item) => item.step_id === runIds.openTicket)
          ? 'automatic'
          : 'manual',
      },
    ],
    issues: [],
    blockers,
  }
}

/** The guided bindings for the golden scenario, as an operator (or a suggestion) sets them. */
export const guidedBindings = {
  observations: [
    {
      step_id: runIds.readOccupancy,
      field: 'occupancy_percent',
      operator: 'gt',
      value: 85,
      interval_seconds: 10,
      timeout_seconds: 600,
      max_samples: 60,
    },
  ],
  objectives: [
    {
      objective_id: runIds.breach,
      step_id: runIds.readOccupancy,
      field: 'occupancy_percent',
      operator: 'gt',
      value: 85,
      anchor_step_id: runIds.raise,
      anchor_field: 'committed_at',
      within_seconds: 120,
    },
    {
      objective_id: runIds.acknowledged,
      step_id: runIds.readTicket,
      field: 'acknowledged',
      operator: 'eq',
      value: true,
      anchor_step_id: runIds.openTicket,
      anchor_field: 'created_at',
      within_seconds: 600,
      source_time_field: 'acknowledged_at',
    },
  ],
  recovery: [
    {
      step_id: runIds.openTicket,
      binding: {
        configuration_id: runIds.restConfiguration,
        operation_key: 'ticket.close',
        operation_version: '1',
      },
      parameters: {
        record_id: { source_step_id: runIds.openTicket, field: 'record_id' },
        run_id: { source_step_id: runIds.openTicket, field: 'run_id' },
        expected_version: { source_step_id: runIds.openTicket, field: 'record_version' },
      },
      ownership_parameter: 'record_id',
      version_parameter: 'expected_version',
    },
  ],
} satisfies Pick<Schemas['RunCreate'], 'observations' | 'objectives' | 'recovery'>

/** A reviewed suggestion as the service returns it. Test fixture only; no model is called. */
export function runCheckSuggestion(
  changes: Partial<Schemas['RunCheckSuggestionView']> = {},
): Schemas['RunCheckSuggestionView'] {
  const valid = <T>(item: T) => ({ item, valid: true, issues: [] })
  return {
    id: '31000000-0000-4000-8000-000000000020',
    preview_id: runIds.preview,
    prompt: 'Suggest checks for every goal.',
    status: 'proposed',
    error: null,
    created_at: date,
    summary:
      'Watch occupancy until it passes 85%, judge both goals from recorded times, and close the exercise ticket afterwards.',
    observations: guidedBindings.observations.map(valid),
    objectives: guidedBindings.objectives.map(valid),
    recovery: guidedBindings.recovery.map(valid),
    questions: ['Is there a recorded result that shows the cots arrived?'],
    is_current: true,
    ...changes,
  }
}

export async function runSetupFixture(
  page: Page,
  options: {
    idempotencyLiteral?: boolean
    blockers?: Schemas['RunBlocker'][]
    suggestions?: Schemas['RunCheckSuggestionView'][]
  } = {},
) {
  const { board, preview, configurations } = runSetupBoard(options)
  const requests: { method: string; path: string; body: unknown; ifMatch?: string }[] = []
  const unhandled: string[] = []
  const suggestions = [...(options.suggestions ?? [])]
  // An accepted request is reported running once, then proposed, as the worker would.
  let pending: { id: string; prompt: string; polls: number } | undefined
  await page.route('**/api/**', async (route) => {
    const request: Request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    const root = `/api/workspaces/${runIds.workspace}`
    const respond = (body: unknown, status = 200, version?: number) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: version === undefined ? {} : { ETag: `"${version}"` },
        body: JSON.stringify(body),
      })
    if (method !== 'GET')
      requests.push({
        method,
        path,
        body: request.postDataJSON(),
        ifMatch: request.headers()['if-match'],
      })
    if (path === '/api/me')
      return respond({ object_id: runIds.operator, organization_admin: false })
    if (path === '/api/workspaces')
      return respond([{ id: runIds.workspace, name: 'Exercise operations', role: 'editor' }])
    if (path === `${root}/execution-grants`)
      return respond([
        {
          id: '31000000-0000-4000-8000-0000000000cc',
          workspace_id: runIds.workspace,
          object_id: runIds.operator,
          capability: 'operator',
          granted_by: runIds.operator,
          granted_at: date,
        },
      ])
    if (path === `${root}/connections`)
      return respond(
        configurations.map((item) => ({
          id: item.connection_id,
          name: item.connection_name,
          kind: item.connection_kind,
          scope: 'workspace',
          environment_id: runIds.environment,
          description: 'Test fixture only.',
          status: 'inventory_only',
        })),
      )
    const configurationsFor = configurations.filter((item) =>
      path.startsWith(`${root}/connections/${item.connection_id}/configurations`),
    )
    if (configurationsFor.length) return respond(configurationsFor)
    const boardPath = `${root}/boards/${runIds.board}`
    if (path === boardPath) return respond(board, 200, board.version)
    if (path === `${boardPath}/previews`) return respond([preview])
    if (path === `${boardPath}/approvals`) return respond([])
    if (path === `${boardPath}/runs` && method === 'GET') return respond([])
    if (path === `${boardPath}/run-setup/suggestions` && method === 'POST') {
      const body = request.postDataJSON() as Schemas['RunCheckRequestInput']
      pending = { id: body.request_id, prompt: body.prompt, polls: 0 }
      return respond({ id: body.request_id, status: 'queued' }, 202)
    }
    if (path === `${boardPath}/run-setup/suggestions`) {
      if (pending && pending.polls++ > 0) {
        suggestions.unshift(runCheckSuggestion({ id: pending.id, prompt: pending.prompt }))
        pending = undefined
      }
      const active = pending
        ? [
            runCheckSuggestion({
              id: pending.id,
              prompt: pending.prompt,
              status: 'running',
              summary: null,
              observations: [],
              objectives: [],
              recovery: [],
              questions: [],
            }),
          ]
        : []
      return respond([...active, ...suggestions])
    }
    if (path === `${boardPath}/runs/preflight`)
      return respond(preflightView(request.postDataJSON(), preview, options.blockers))
    if (path === `${boardPath}/runs` && method === 'POST')
      return respond({ id: runIds.run }, 201, 1)
    unhandled.push(`${method} ${path}`)
    return respond({ detail: `Unhandled run setup fixture: ${method} ${path}` }, 404)
  })
  return { requests, unhandled, board, preview }
}
