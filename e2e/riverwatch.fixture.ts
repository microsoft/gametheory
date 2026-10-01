import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { Page } from '@playwright/test'
import type { components } from '../apps/web/src/api.generated'
import type {
  Approval,
  BoardDraft,
  BoardView,
  Configuration,
  Preview,
} from '../apps/web/src/preparation'
import type {
  Asset,
  Comment,
  Connection,
  Content,
  Environment,
  Planning,
  Revision,
  Scenario,
  Workspace,
} from '../apps/web/src/types'

/*
 * The synthetic Riverwatch flood exercise, served through mocked API routes for the README
 * screenshot scenario. TEST FIXTURE ONLY: every person, place, identifier, approval, and run
 * result is fictional. The narrative, operation catalogs, and asset checksums are read from the
 * independent exercise package, exactly as a user would register and upload them through the UI.
 */
type Schemas = components['schemas']
type Json = Record<string, unknown>

const exercise = join(__dirname, '..', 'exercises', 'flood-response', 'assets')
const readText = (name: string) => readFileSync(join(exercise, name), 'utf8')
const readJson = <T>(name: string): T => JSON.parse(readText(name)) as T

/** Stable, realistic-looking v4-format identifiers derived from a name. */
export function uuid(name: string) {
  const hex = createHash('sha256').update(`riverwatch:${name}`).digest('hex')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-8${hex.slice(17, 20)}-${hex.slice(20, 32)}`
}
const digest = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex')

/** The frozen browser clock for every screenshot, so relative times and expiries are stable. */
export const now = '2026-09-22T15:05:00Z'
const at = (time: string) => `2026-09-22T${time}Z`

export const ids = {
  workspace: uuid('workspace'),
  scenario: uuid('scenario'),
  board: uuid('board'),
  run: uuid('run'),
  owner: uuid('person:scenario-owner'),
  reviewer: uuid('person:preparation-reviewer'),
  lab: uuid('environment:flood-lab'),
  production: uuid('environment:production'),
  sqlConnection: uuid('connection:sql'),
  restConnection: uuid('connection:rest'),
  graphConnection: uuid('connection:graph'),
  sqlConfiguration: uuid('configuration:sql'),
  restConfiguration: uuid('configuration:rest'),
  preview: uuid('preview:3'),
  approval: uuid('approval:1'),
  suggestion: uuid('suggestion:1'),
  detect: uuid('objective:detect'),
  acknowledge: uuid('objective:acknowledge'),
  allocate: uuid('objective:allocate'),
  readSeed: uuid('step:read-seed'),
  inject: uuid('step:inject'),
  observe: uuid('step:observe'),
  request: uuid('step:request'),
  milestones: uuid('step:milestones'),
}

const seed = readJson<{
  run_id: string
  shelters: { key: string; id: string; record_version: string }[]
}>('seed-manifest-example.json')
const labRun = seed.run_id
const aster = seed.shelters.find((shelter) => shelter.key === 'aster-reach')!
const labRequest = uuid('lab:cots-request')

const workspace: Workspace = { id: ids.workspace, name: 'Riverwatch flood exercise', role: 'owner' }
const environments: Environment[] = [
  { id: ids.lab, name: 'Flood lab' },
  { id: ids.production, name: 'Production' },
]
const connections: Connection[] = [
  {
    id: ids.sqlConnection,
    name: 'Flood lab SQL',
    kind: 'sql',
    scope: 'workspace',
    environment_id: ids.lab,
    description: 'Synthetic shelter capacity database in the independent flood lab.',
    status: 'inventory_only',
  },
  {
    id: ids.restConnection,
    name: 'Flood lab request API',
    kind: 'rest',
    scope: 'workspace',
    environment_id: ids.lab,
    description: 'Resource requests that participants acknowledge in the lab UI.',
    status: 'inventory_only',
  },
  {
    id: ids.graphConnection,
    name: 'Exercise notifications',
    kind: 'graph',
    scope: 'workspace',
    environment_id: ids.lab,
    description: 'Description-only notification templates. Sending is absent.',
    status: 'inventory_only',
  },
]

const manifest = readJson<{
  files: { path: string; media_type: string; size_bytes: number; sha256: string }[]
}>('manifest.json')
const uploaded = [
  'situation-brief.md',
  'objectives-and-evidence.md',
  'inject-schedule.csv',
  'shelters.json',
  'personnel.json',
  'request-samples.json',
  'profile.json',
  'notification-initial.txt',
  'notification-escalation.txt',
]
const assets: Asset[] = uploaded.map((name, index) => {
  const file = manifest.files.find((item) => item.path === name)!
  return {
    id: uuid(`asset:${name}`),
    name,
    media_type: file.media_type,
    sha256: file.sha256,
    size: file.size_bytes,
    state: 'ready',
    previous_id: null,
    actor: ids.owner,
    created_at: at(`09:${String(12 + index).padStart(2, '0')}:00`),
  }
})

/** Converts the exercise's plain Markdown brief into the studio's rich document. */
function briefDocument(markdown: string): Content['document'] {
  const text = (value: string) => value.replace(/\s*\n\s*/g, ' ').trim()
  const paragraph = (value: string) => ({
    type: 'paragraph',
    content: [{ type: 'text', text: value }],
  })
  const list = (type: string, items: string[], attrs?: Json) => ({
    type,
    ...(attrs && { attrs }),
    content: items.map((item) => ({ type: 'listItem', content: [paragraph(item)] })),
  })
  const content: Json[] = []
  for (const block of markdown.trim().split(/\n\s*\n/)) {
    if (block.startsWith('# ')) continue
    if (block.startsWith('## '))
      content.push({
        type: 'heading',
        attrs: { level: 2 },
        content: [{ type: 'text', text: text(block.slice(3)) }],
      })
    else if (block.startsWith('- ')) {
      const items = block.split(/\n(?=- )/).map((item) => text(item.slice(2)))
      content.push(list('bulletList', items))
    } else if (/^\d+\. /.test(block)) {
      const items = block.split(/\n(?=\d+\. )/).map((item) => text(item.replace(/^\d+\. /, '')))
      // The editor's canonical JSON, so loading the document leaves the draft unchanged.
      content.push(list('orderedList', items, { start: 1, type: null }))
      // Embed the scenario flow right after the brief's numbered exercise flow.
      content.push({ type: 'reference', attrs: { kind: 'flow', targetId: 'scenario' } })
    } else content.push(paragraph(text(block)))
  }
  return { type: 'doc', content }
}

const node = (
  name: string,
  kind: Schemas['FlowNode']['kind'],
  label: string,
  detail: string,
  connection: Connection | undefined,
  x: number,
  y: number,
): Schemas['FlowNode'] => ({
  id: uuid(`node:${name}`),
  kind,
  label,
  detail,
  connection_id: connection?.id ?? null,
  environment_id: connection?.environment_id ?? null,
  position: { x, y },
})
const [sqlConnection, restConnection, graphConnection] = connections
const nodes = [
  node(
    'inject',
    'action',
    'Inject occupancy',
    'An authorized injector commits Aster Reach School occupancy of 108 of 120.',
    sqlConnection,
    215,
    0,
  ),
  node(
    'detect',
    'condition',
    'Occupancy above 85%?',
    'Compare occupancy_percent with the literal 85 using greater-than. Exactly 85 is not a trigger.',
    sqlConnection,
    215,
    105,
  ),
  node(
    'request',
    'action',
    'Create cots request',
    'An authorized service creates an EXERCISE ONLY request for 24 cots.',
    restConnection,
    215,
    225,
  ),
  node(
    'acknowledge',
    'wait',
    'Await acknowledgement',
    'A participant acknowledges the request in the independent operations UI within 10 minutes.',
    restConnection,
    215,
    330,
  ),
  node(
    'allocate',
    'condition',
    'Allocated in 20 min?',
    'Committed allocations must meet the requested quantity within 20 minutes of request creation.',
    restConnection,
    0,
    455,
  ),
  node(
    'escalate',
    'action',
    'Escalate once',
    'Describe at most one escalation, then assess allocation. Graph sending is absent.',
    graphConnection,
    430,
    455,
  ),
]
const edge = (source: number, target: number, label = ''): Schemas['FlowEdge'] => ({
  id: uuid(`edge:${source}-${target}`),
  source: nodes[source].id,
  target: nodes[target].id,
  label,
})
const objectives: Schemas['Objective'][] = [
  {
    id: ids.detect,
    title: 'Detect the capacity breach',
    criterion:
      'Read occupancy_percent strictly greater than 85 within 2 minutes of the committed occupancy event. Missing event times leave timing indeterminate.',
  },
  {
    id: ids.acknowledge,
    title: 'Acknowledge the request',
    criterion:
      'A participant acknowledges the resource request within 10 minutes of its committed creation.',
  },
  {
    id: ids.allocate,
    title: 'Allocate the requested quantity',
    criterion:
      'Committed allocations meet the requested quantity within 20 minutes of request creation. A partial allocation alone does not meet this objective.',
  },
]
const content: Content = {
  schema_version: 1,
  title: 'Riverwatch: shelter capacity and escalation',
  document: briefDocument(readText('situation-brief.md')),
  objectives,
  nodes,
  edges: [
    edge(0, 1),
    edge(1, 2, 'Above 85%'),
    edge(2, 3),
    edge(3, 4, 'Acknowledged'),
    edge(3, 5, 'No acknowledgement'),
  ],
  asset_ids: assets.map((asset) => asset.id),
}
const scenario: Scenario = {
  id: ids.scenario,
  workspace_id: ids.workspace,
  version: 5,
  updated_at: at('11:40:00'),
  content,
}
const revisions: Revision[] = [
  { version: 2, actor: ids.owner, created_at: at('11:45:00'), content },
  { version: 1, actor: ids.owner, created_at: at('10:05:00'), content: { ...content, edges: [] } },
]
const planning: Planning[] = [
  {
    id: uuid('planning:objectives'),
    prompt: 'Draft objectives from the evidence criteria in the brief.',
    actor: ids.owner,
    base_version: 3,
    status: 'applied',
    error: null,
    created_at: at('10:20:00'),
    proposal: {
      summary:
        'Added three measurable objectives anchored to committed lab events, each with an inclusive deadline.',
      content,
    },
  },
  {
    id: uuid('planning:acknowledgement'),
    prompt:
      'Name the evidence we will read for acknowledgement, and say what happens when it is missing.',
    actor: ids.owner,
    base_version: 5,
    status: 'proposed',
    error: null,
    created_at: at('11:52:00'),
    proposal: {
      summary:
        'Cites the lab’s acknowledged_on_time verdict and request.acknowledge event as evidence, and keeps an absent verdict indeterminate rather than a participant failure.',
      content: {
        ...content,
        objectives: objectives.map((objective) =>
          objective.id === ids.acknowledge
            ? {
                ...objective,
                criterion:
                  'The lab’s acknowledged_on_time verdict is true: a request.acknowledge event committed within 10 minutes of creation. An absent verdict stays indeterminate.',
              }
            : objective,
        ),
      },
    },
  },
]
const comments: Comment[] = [
  {
    id: uuid('comment:detect'),
    body: 'Detection starts at the committed inject event, not the scheduled inject time.',
    actor: ids.reviewer,
    base_version: 4,
    created_at: at('11:30:00'),
  },
]

const catalogs = {
  sql: readJson<Schemas['OperationCatalog']>('operation-catalog-sql.json'),
  rest: readJson<Schemas['OperationCatalog']>('operation-catalog-rest.json'),
}
function configuration(kind: 'sql' | 'rest'): Configuration {
  const sql = kind === 'sql'
  const content: Schemas['ConnectionConfiguration'] = {
    schema_version: 'connection-configuration/v1',
    classification: 'nonproduction',
    resource_id: sql ? 'flood-lab/sql' : 'flood-lab/request-api',
    endpoint: sql ? 'sql.flood-lab.example' : 'https://api.flood-lab.example',
    database: sql ? 'flood_lab' : '',
    identity_ref: sql ? 'flood-lab/injector' : 'flood-lab/request-service',
    catalog: catalogs[kind],
    notification: null,
  }
  const connection = sql ? sqlConnection : restConnection
  return {
    id: sql ? ids.sqlConfiguration : ids.restConfiguration,
    workspace_id: ids.workspace,
    connection_id: connection.id,
    version: sql ? 2 : 1,
    content,
    digest: digest(content),
    connection_kind: kind,
    connection_name: connection.name,
    environment_id: ids.lab,
    environment_name: 'Flood lab',
    template_asset: null,
    created_by: ids.owner,
    created_at: at(sql ? '09:40:00' : '09:35:00'),
    execution_authorized: false,
    withdrawn_at: null,
    withdrawn_by: null,
  }
}
const configurations = [configuration('sql'), configuration('rest')]
const binding = (kind: 'sql' | 'rest', key: string, version: string) => ({
  configuration_id: kind === 'sql' ? ids.sqlConfiguration : ids.restConfiguration,
  operation_key: key,
  operation_version: version,
})
const step = (
  id: string,
  label: string,
  authoringNode: number | null,
  dependsOn: string[],
  operation: ReturnType<typeof binding>,
  parameters: NonNullable<Schemas['PreparationStep']['parameters']>,
): Schemas['PreparationStep'] => ({
  id,
  label,
  kind: 'operation',
  authoring_node_id: authoringNode === null ? null : nodes[authoringNode].id,
  depends_on: dependsOn,
  binding: operation,
  parameters,
  wait_seconds: null,
  condition: null,
})
const shelter = { run_id: labRun, shelter_id: aster.id }
const draft: BoardDraft = {
  schema_version: 'exercise-preparation-draft/v1',
  name: 'Riverwatch shelter surge drill',
  notification_budget: 0,
  recovery:
    'The lab operator previews and applies seed recovery for this run. Only unchanged seed-owned records are retired; participant changes, events, and receipts are retained.',
  window: { starts_at: at('14:00:00'), ends_at: at('17:00:00') },
  steps: [
    step(
      ids.readSeed,
      'Read seeded occupancy',
      null,
      [],
      binding('sql', 'shelter.occupancy.read', '2'),
      shelter,
    ),
    step(
      ids.inject,
      'Inject Aster Reach occupancy',
      0,
      [ids.readSeed],
      binding('sql', 'shelter.occupancy.update', '2'),
      {
        ...shelter,
        occupancy: 108,
        expected_version: { source_step_id: ids.readSeed, field: 'record_version' },
      },
    ),
    step(
      ids.observe,
      'Read occupancy after inject',
      1,
      [ids.inject],
      binding('sql', 'shelter.occupancy.read', '2'),
      shelter,
    ),
    step(
      ids.request,
      'Create cots request',
      2,
      [ids.observe],
      binding('rest', 'resource-request.create', '1'),
      {
        ...shelter,
        resource_type: 'cots',
        quantity_requested: 24,
        summary: 'EXERCISE ONLY — additional cots for Aster Reach School',
        needed_by: at('15:00:00'),
      },
    ),
    step(
      ids.milestones,
      'Read request milestones',
      3,
      [ids.request],
      binding('rest', 'resource-request.milestones', '1'),
      {
        run_id: labRun,
        request_id: { source_step_id: ids.request, field: 'request_id' },
        acknowledge_within_seconds: 600,
        allocate_within_seconds: 1200,
      },
    ),
  ],
}
const pinnedScenario: Schemas['ScenarioPin'] = {
  scenario_id: ids.scenario,
  revision_version: 2,
  content,
}
const pinnedAssets: Schemas['AssetPin'][] = assets.map(
  ({ id, name, media_type, sha256, size }) => ({
    id,
    name,
    media_type,
    sha256,
    size,
  }),
)
const preparationManifest: Schemas['PreparationManifest'] = {
  schema_version: 'exercise-preparation/v1',
  board_id: ids.board,
  workspace_id: ids.workspace,
  board_version: 6,
  draft,
  scenario: pinnedScenario,
  assets: pinnedAssets,
  configurations: configurations.map(
    ({ withdrawn_at: _at, withdrawn_by: _by, ...snapshot }) => snapshot,
  ),
  execution_authorized: false,
}

/** The static findings the service computes for this manifest (see backend preparation.py). */
function previewFindings(value: Schemas['PreparationManifest']): Preview['findings'] {
  const findings: Preview['findings'] = []
  for (const item of value.draft.steps ?? []) {
    const configured = value.configurations.find(
      (config) => config.id === item.binding?.configuration_id,
    )
    const operation = configured?.content.catalog.operations.find(
      (entry) =>
        entry.key === item.binding?.operation_key &&
        entry.version === item.binding?.operation_version,
    )
    for (const field of operation?.parameters ?? []) {
      const supplied = item.parameters?.[field.name]
      const path = `/draft/steps/${item.id}/parameters/${field.name}`
      if (field.required && supplied == null)
        findings.push({
          code: 'missing_parameter',
          severity: 'blocker',
          message: `Required parameter ${field.name} is unresolved.`,
          path,
          step_id: item.id,
        })
      else if (typeof supplied === 'object' && supplied !== null)
        findings.push({
          code: 'result_binding_unverified',
          severity: 'warning',
          message:
            'This value refers to a declared earlier result; no operation has run or returned it.',
          path,
          step_id: item.id,
        })
    }
  }
  for (const config of value.configurations) {
    const path = `/configurations/${config.id}`
    findings.push(
      {
        code: 'target_contents_unverified',
        severity: 'warning',
        message:
          'Target contents and record identifiers have not been checked against a live system.',
        path,
      },
      {
        code: 'connectivity_unverified',
        severity: 'warning',
        message:
          'Target connectivity has not been verified; registration makes no network requests.',
        path,
      },
      {
        code: 'permissions_unverified',
        severity: 'warning',
        message: 'Effective target permissions have not been verified.',
        path,
      },
    )
  }
  findings.push(
    {
      code: 'live_readiness_unverified',
      severity: 'warning',
      message: 'This static preview is not evidence of live readiness or execution.',
      path: '',
    },
    {
      code: 'execution_disabled',
      severity: 'blocker',
      message:
        'Execution is disabled for this preparation preview. Only the separate run workflow can evaluate execution authority.',
      path: '',
    },
  )
  return findings
}
const preview: Preview = {
  id: ids.preview,
  board_id: ids.board,
  board_version: 6,
  sequence: 3,
  digest: digest(preparationManifest),
  manifest: preparationManifest,
  findings: previewFindings(preparationManifest),
  created_by: ids.owner,
  created_at: at('12:10:00'),
  is_current: true,
  execution_authorized: false,
  execution_eligible: false,
}
const approval: Approval = {
  id: ids.approval,
  board_id: ids.board,
  board_version: 6,
  preview_id: ids.preview,
  digest: preview.digest,
  sequence: 1,
  kind: 'preparation',
  execution_authorized: false,
  reviewer: ids.reviewer,
  decision: 'approved',
  acknowledge_unverified: true,
  expires_at: '2026-09-29T17:00:00Z',
  note: 'Bounds, recovery, and the 85% threshold match the published brief. Live readiness is still unverified.',
  created_at: at('13:15:00'),
  validity: { valid: true, reasons: [] },
  revoked_at: null,
  revoked_by: null,
}
const board: BoardView = {
  id: ids.board,
  workspace_id: ids.workspace,
  name: draft.name,
  version: 6,
  scenario_id: ids.scenario,
  revision_version: 2,
  created_by: ids.owner,
  created_at: at('11:58:00'),
  updated_at: at('12:08:00'),
  preparation_status: 'previewed',
  approval_status: 'approved',
  execution_authorized: false,
  draft,
  scenario: pinnedScenario,
  assets: pinnedAssets,
  contributors: [ids.owner],
  latest_preview: preview,
  current_approval: approval,
  can_edit: true,
  can_approve: false,
  approval_blockers: ['creator_or_contributor'],
}

const observations: Schemas['Observation'][] = [
  {
    step_id: ids.observe,
    field: 'occupancy_percent',
    operator: 'gt',
    value: 85,
    interval_seconds: 10,
    timeout_seconds: 600,
    max_samples: 60,
  },
  {
    step_id: ids.milestones,
    field: 'allocated_on_time',
    operator: 'eq',
    value: true,
    interval_seconds: 30,
    timeout_seconds: 1500,
    max_samples: 60,
  },
]
const objectiveRules: Schemas['ObjectiveRule'][] = [
  {
    objective_id: ids.detect,
    step_id: ids.observe,
    field: 'occupancy_percent',
    operator: 'gt',
    value: 85,
    anchor_step_id: ids.inject,
    anchor_field: 'committed_at',
    within_seconds: 120,
  },
  {
    objective_id: ids.acknowledge,
    step_id: ids.milestones,
    field: 'acknowledged_on_time',
    operator: 'eq',
    value: true,
  },
  {
    objective_id: ids.allocate,
    step_id: ids.milestones,
    field: 'allocated_on_time',
    operator: 'eq',
    value: true,
  },
]
const valid = <T>(item: T) => ({ item, valid: true, issues: [] })
const suggestion: Schemas['RunCheckSuggestionView'] = {
  id: ids.suggestion,
  preview_id: ids.preview,
  prompt:
    'Watch occupancy until it passes 85%, then judge detection, acknowledgement, and allocation from the lab’s own times.',
  status: 'proposed',
  error: null,
  created_at: at('14:12:00'),
  summary:
    'Watch occupancy until it passes 85% and read the request milestones until allocation is decided. Detection is timed from the committed inject; acknowledgement and allocation use the lab’s verdicts.',
  observations: observations.map(valid),
  objectives: objectiveRules.map(valid),
  recovery: [],
  questions: [
    'Neither write has a registered undo operation. Should the lab operator’s seed recovery be recorded as a manual report?',
  ],
  is_current: true,
}

const labPolicy: Schemas['EnvironmentPolicyView'] = {
  environment_id: ids.lab,
  name: 'Flood lab',
  classification: 'nonproduction',
  execution_enabled: true,
  approval_required: false,
  version: 3,
  updated_by: ids.owner,
  updated_at: at('08:30:00'),
}
const policies: Schemas['EnvironmentPolicyView'][] = [
  labPolicy,
  {
    environment_id: ids.production,
    name: 'Production',
    classification: 'production',
    execution_enabled: false,
    approval_required: true,
    version: 1,
    updated_by: ids.owner,
    updated_at: at('08:25:00'),
  },
]
const grants: Schemas['ExecutionGrantView'][] = [
  {
    id: uuid('grant:operator'),
    workspace_id: ids.workspace,
    object_id: ids.owner,
    capability: 'operator',
    granted_by: ids.owner,
    granted_at: at('08:40:00'),
  },
  {
    id: uuid('grant:reviewer'),
    workspace_id: ids.workspace,
    object_id: ids.reviewer,
    capability: 'reviewer',
    granted_by: ids.owner,
    granted_at: at('08:41:00'),
  },
]

const injectedAt = at('14:30:04.412')
const requestedAt = at('14:30:21.087')
const injected = {
  ...shelter,
  occupancy: 108,
  capacity: 120,
  record_version: uuid('lab:aster-version-2'),
  occupancy_percent: 90,
  durable_event_id: uuid('lab:inject-event'),
  committed_at: injectedAt,
}
const stepResults: Record<string, Schemas['RunStepView']['result']> = {
  [ids.readSeed]: {
    ...shelter,
    occupancy: 84,
    capacity: 120,
    record_version: aster.record_version,
    occupancy_percent: 70,
    durable_event_id: null,
    committed_at: null,
  },
  [ids.inject]: { ...injected, outcome: 'succeeded', correlation_id: uuid('lab:inject') },
  [ids.observe]: injected,
  [ids.request]: {
    request_id: labRequest,
    ...shelter,
    record_version: uuid('lab:request-version-1'),
    status: 'open',
    quantity_requested: 24,
    quantity_allocated: 0,
    created_at: requestedAt,
    needed_by: at('15:00:00'),
    outcome: 'succeeded',
    durable_event_id: uuid('lab:request-event'),
    committed_at: requestedAt,
    correlation_id: uuid('lab:request'),
  },
  [ids.milestones]: {
    contract_version: 'flood-lab-milestones/v1',
    request_id: labRequest,
    run_id: labRun,
    record_version: uuid('lab:request-version-4'),
    status: 'acknowledged',
    quantity_requested: 24,
    quantity_allocated: 16,
    created_at: requestedAt,
    created_event_id: uuid('lab:request-event'),
    acknowledged: true,
    acknowledged_at: at('14:36:48.530'),
    acknowledgement_event_id: uuid('lab:acknowledge-event'),
    allocated_total_by_deadline: 16,
    allocation_completed_at: null,
    allocation_completed_event_id: null,
    allocation_event_count: 2,
    acknowledgement_deadline: at('14:40:21.087'),
    allocation_deadline: at('14:50:21.087'),
    as_of: at('14:55:22.301'),
    acknowledged_on_time: true,
    allocated_on_time: false,
    acknowledgement_reason: 'Acknowledged at or before the inclusive deadline.',
    allocation_reason: 'Committed allocations total 16 of 24 after the inclusive deadline.',
  },
}
const stepTimes: Record<string, [string, string, number]> = {
  [ids.readSeed]: ['14:30:01', '14:30:02', 1],
  [ids.inject]: ['14:30:03', '14:30:04', 1],
  [ids.observe]: ['14:30:15', '14:30:16', 1],
  [ids.request]: ['14:30:20', '14:30:21', 1],
  [ids.milestones]: ['14:30:22', '14:55:22', 51],
}
const event = (
  name: string,
  kind: string,
  time: string,
  detail: Schemas['RunEventView']['detail'],
  stepId: string | null = null,
): Schemas['RunEventView'] => ({
  id: uuid(`event:${name}`),
  kind,
  step_id: stepId,
  created_at: at(time),
  detail: { phase: 'exercise', ...detail },
})
const attempts = (draft.steps ?? []).flatMap((item) => {
  const [started, finished] = stepTimes[item.id]
  const attempt = uuid(`attempt:${item.id}`)
  return [
    event(`${item.id}:started`, 'attempt.started', started, { attempt_id: attempt }, item.id),
    event(
      `${item.id}:succeeded`,
      'operation.succeeded',
      finished,
      { attempt_id: attempt, result: JSON.stringify(stepResults[item.id]), reason: null },
      item.id,
    ),
  ]
})
const events: Schemas['RunEventView'][] = [
  event('prepared', 'run.prepared', '14:24:10', { actor: ids.owner, preview_id: ids.preview }),
  event('control.authorize', 'control.authorize', '14:26:02', {
    actor: ids.owner,
    note: 'Readiness receipts cover the 14:00–17:00 UTC window.',
  }),
  event('authorized', 'run.authorized', '14:26:02', {
    actor: ids.owner,
    context_id: uuid('authorization-context'),
    approval_required: false,
  }),
  event('control.start', 'control.start', '14:29:58', {
    actor: ids.owner,
    note: 'Starting the Aster Reach surge inject.',
  }),
  ...attempts.slice(0, 6),
  event('observation:occupancy', 'observation', '14:30:16', { matched: true }, ids.observe),
  ...attempts.slice(6, 8),
  attempts[8],
  event('observation:acknowledged', 'observation', '14:36:52', { matched: false }, ids.milestones),
  event('observation:deadline', 'observation', '14:50:52', { matched: false }, ids.milestones),
  attempts[9],
  event(
    'observation:ended',
    'observation.ended',
    '14:55:22',
    {
      reason:
        'Observation budget ended without a matching sample; missing coverage is not participant failure.',
    },
    ids.milestones,
  ),
  event('finished', 'run.finished', '14:55:23', { state: 'completed' }),
]
const evidence = (...names: string[]) => names.map((name) => uuid(`event:${name}`))
const runManifest: Schemas['RunManifest'] = {
  schema_version: 'exercise-execution/v2',
  run_id: ids.run,
  preparation: preparationManifest,
  trigger: 'manual',
  observations,
  objectives: objectiveRules,
  recovery: [],
  sql_idempotency: 'dispatcher-owned/v1',
  max_operations: 1000,
}
function authority(config: Configuration): Schemas['TargetAuthorityView'] {
  return {
    configuration_id: config.id,
    resource_id: config.content.resource_id,
    endpoint: config.content.endpoint,
    database: config.content.database,
    identity_ref: config.content.identity_ref,
    client_id: uuid(`client:${config.connection_kind}`),
    token_scope: config.connection_kind === 'rest' ? 'api://flood-lab-api/.default' : '',
    operation_digests: config.content.catalog.operations.map((operation) => digest(operation)),
    replayable_operations: [],
  }
}
const run: Schemas['RunView'] = {
  id: ids.run,
  board_id: ids.board,
  version: 41,
  state: 'completed',
  phase: 'exercise',
  operator: ids.owner,
  created_at: at('14:24:10'),
  manifest_digest: digest(runManifest),
  manifest: runManifest,
  context_id: uuid('authorization-context'),
  authorization: {
    id: uuid('authorization-context'),
    created_by: ids.owner,
    created_at: at('14:26:02'),
    policies: [labPolicy],
    targets: configurations.map(authority),
    readiness: configurations.map((config) => ({
      id: uuid(`readiness:${config.connection_kind}`),
      configuration_id: config.id,
      checked_at: at('13:50:00'),
      expires_at: at('18:00:00'),
      evidence_reference: `flood-lab runbook readiness check · ${config.connection_name}`,
      operator: 'lab-deployment-operator',
    })),
  },
  approval_required: false,
  approval_status: 'not_required',
  blockers: [],
  blocker_details: [],
  can_operate: true,
  can_stop: true,
  can_review: false,
  steps: (draft.steps ?? []).map((item) => {
    const [started, finished, samples] = stepTimes[item.id]
    return {
      step_id: item.id,
      phase: 'exercise',
      state: 'succeeded',
      result: stepResults[item.id],
      reason:
        item.id === ids.milestones
          ? 'Observation budget ended without a matching sample; missing coverage is not participant failure.'
          : null,
      samples,
      started_at: at(started),
      finished_at: at(finished),
    }
  }),
  events,
  findings: [
    {
      objective_id: ids.detect,
      state: 'met',
      reason: 'Evidence meets the predicate at or before the inclusive deadline.',
      evidence_ids: evidence(`${ids.inject}:succeeded`, 'observation:occupancy'),
    },
    {
      objective_id: ids.acknowledge,
      state: 'met',
      reason: 'The recorded observation meets the predicate.',
      evidence_ids: evidence('observation:acknowledged'),
    },
    {
      objective_id: ids.allocate,
      state: 'unmet',
      reason: 'The recorded observation does not meet the predicate.',
      evidence_ids: evidence('observation:deadline', `${ids.milestones}:succeeded`),
    },
  ],
}
const runSummary: Schemas['RunSummary'] = {
  id: run.id,
  board_id: run.board_id,
  state: run.state,
  phase: run.phase,
  operator: run.operator,
  created_at: run.created_at,
}

export async function riverwatchFixture(page: Page) {
  const unhandled: string[] = []
  await page.clock.setFixedTime(now)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    const root = `/api/workspaces/${ids.workspace}`
    const respond = (body: unknown, version?: number) =>
      route.fulfill({
        contentType: 'application/json',
        headers: version === undefined ? {} : { ETag: `"${version}"` },
        body: JSON.stringify(body),
      })
    if (method === 'GET') {
      const scenarioPath = `${root}/scenarios/${ids.scenario}`
      const boardPath = `${root}/boards/${ids.board}`
      const policy = policies.find(
        (item) => path === `/api/admin/environment-policies/${item.environment_id}`,
      )
      const routes: Record<string, () => Promise<void>> = {
        '/api/me': () => respond({ object_id: ids.owner, organization_admin: true }),
        '/api/workspaces': () => respond([workspace]),
        '/api/environments': () => respond(environments),
        [`${root}/scenarios`]: () => respond([scenario]),
        [scenarioPath]: () => respond(scenario, scenario.version),
        [`${scenarioPath}/planning`]: () => respond(planning),
        [`${scenarioPath}/comments`]: () => respond(comments),
        [`${scenarioPath}/revisions`]: () => respond(revisions),
        [`${root}/assets`]: () => respond(assets),
        [`${root}/connections`]: () => respond(connections),
        [`${root}/connections/${ids.sqlConnection}/configurations`]: () =>
          respond([configurations[0]]),
        [`${root}/connections/${ids.restConnection}/configurations`]: () =>
          respond([configurations[1]]),
        [`${root}/connections/${ids.graphConnection}/configurations`]: () => respond([]),
        [`${root}/execution-grants`]: () => respond(grants),
        [boardPath]: () => respond(board, board.version),
        [`${boardPath}/previews`]: () => respond([preview]),
        [`${boardPath}/approvals`]: () => respond([approval]),
        [`${boardPath}/runs`]: () => respond([runSummary]),
        [`${boardPath}/run-setup/suggestions`]: () => respond([suggestion]),
        [`${root}/runs/${ids.run}`]: () => respond(run, run.version),
        '/api/admin/runtime': () =>
          respond({
            execution_enabled: true,
            sql_configured: true,
            scheduler_configured: true,
            target_bindings_configured: true,
            run_assistant_enabled: true,
            message:
              'Configuration is not live readiness. This is a UI fixture, not a deployed environment.',
          } satisfies Schemas['RuntimeStatus']),
        '/api/admin/environment-policies': () => respond(policies),
      }
      if (routes[path]) return routes[path]()
      if (policy) return respond(policy, policy.version)
    }
    unhandled.push(`${method} ${path}`)
    return route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: `Unhandled Riverwatch fixture: ${method} ${path}` }),
    })
  })
  return { unhandled }
}
