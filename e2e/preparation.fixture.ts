import { createHash } from 'node:crypto'
import { expect, type Page } from '@playwright/test'
import type { components } from '../apps/web/src/api.generated'
import type { Asset, Connection, Revision, Scenario, Workspace } from '../apps/web/src/types'
import type {
  Approval,
  ApproverGrant,
  BoardDraft,
  BoardView,
  Configuration,
  Preview,
} from '../apps/web/src/preparation'

type Schemas = components['schemas']
export const ids = {
  workspace: '11111111-1111-4111-8111-111111111111',
  scenario: '22222222-2222-4222-8222-222222222222',
  board: '66666666-6666-4666-8666-666666666666',
  connection: '77777777-7777-4777-8777-777777777777',
  environment: '88888888-8888-4888-8888-888888888888',
  configuration: '99999999-9999-4999-8999-999999999999',
  admin: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  author: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  reviewer: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  viewer: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  template: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
}

export const serviceCatalog: Schemas['OperationCatalog'] = {
  schema_version: 'operation-catalog/v1',
  name: 'Service ticket operations — test fixture',
  operations: [
    {
      key: 'ticket.create',
      version: '1',
      label: 'Create a service ticket',
      effect: 'write',
      invocation: { kind: 'rest', method: 'POST', path: '/tickets' },
      parameters: [{ name: 'title', type: 'string', required: true, max_length: 80 }],
      results: [
        { name: 'record_id', type: 'uuid', required: true },
        { name: 'record_version', type: 'string', required: true, max_length: 128 },
      ],
      recovery: 'An external operator would review ownership and versions before recovery.',
    },
    {
      key: 'ticket.read',
      version: '1',
      label: 'Read a service ticket',
      effect: 'read',
      invocation: { kind: 'rest', method: 'GET', path: '/tickets/{record_id}' },
      parameters: [{ name: 'record_id', type: 'uuid', required: true }],
      results: [
        { name: 'priority', type: 'integer', required: true, minimum: 0, maximum: 9 },
        { name: 'record_version', type: 'string', required: true, max_length: 128 },
      ],
      recovery: 'Read-only; no target mutation.',
    },
    {
      key: 'ticket.update',
      version: '1',
      label: 'Update ticket capacity',
      effect: 'write',
      invocation: { kind: 'rest', method: 'PATCH', path: '/tickets/{record_id}' },
      parameters: [
        { name: 'record_id', type: 'uuid', required: true },
        { name: 'units', type: 'integer', required: true, minimum: 1, maximum: 20 },
        { name: 'expected_version', type: 'string', required: true, max_length: 128 },
      ],
      results: [{ name: 'record_version', type: 'string', required: true, max_length: 128 }],
      recovery: 'Preserve human changes and retained evidence during external recovery.',
    },
  ],
}

const date = '2026-01-01T12:00:00Z'
function digest(value: unknown) {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex')
}
let nextId = 100
function uuid() {
  return `00000000-0000-4000-8000-${String(++nextId).padStart(12, '0')}`
}
type Profile = 'admin' | 'author' | 'reviewer' | 'viewer'

export async function preparationFixture(
  page: Page,
  options: { profile?: Profile; empty?: boolean; denied?: boolean } = {},
) {
  let profile = options.profile ?? 'author'
  let failSave = false
  let unavailableConfigurations = false
  let connectionAccessible = true
  let executeRequests = 0
  const role = (): Workspace['role'] =>
    profile === 'admin' ? 'owner' : profile === 'author' ? 'editor' : 'viewer'
  const actor = () => ids[profile]
  let scenario: Scenario | undefined = options.empty
    ? undefined
    : {
        id: ids.scenario,
        workspace_id: ids.workspace,
        version: 1,
        updated_at: date,
        content: {
          schema_version: 1,
          title: 'Service recovery review',
          document: {
            type: 'doc',
            content: [
              {
                type: 'paragraph',
                content: [
                  {
                    type: 'text',
                    text: 'A generic service continuity planning fixture, not live target data.',
                  },
                ],
              },
            ],
          },
          objectives: [],
          nodes: [],
          edges: [],
          asset_ids: [],
        },
      }
  let revisions: Revision[] = scenario
    ? [
        {
          version: 1,
          actor: ids.author,
          created_at: date,
          content: structuredClone(scenario.content),
        },
      ]
    : []
  const connections: Connection[] = options.empty
    ? []
    : [
        {
          id: ids.connection,
          name: 'Service ticket API',
          kind: 'rest',
          scope: 'workspace',
          environment_id: ids.environment,
          description: 'Explicit test fixture only.',
          status: 'inventory_only',
        },
      ]
  const assets: Asset[] = [
    {
      id: ids.template,
      name: 'Fixed service notice template',
      media_type: 'text/plain',
      sha256: 'a'.repeat(64),
      size: 24,
      state: 'ready',
      previous_id: null,
      actor: ids.admin,
      created_at: date,
    },
  ]
  const initialConfig: Configuration = {
    id: ids.configuration,
    workspace_id: ids.workspace,
    connection_id: ids.connection,
    version: 1,
    content: {
      schema_version: 'connection-configuration/v1',
      classification: 'nonproduction',
      resource_id: 'service-fixture',
      endpoint: 'http://127.0.0.1:9050',
      database: '',
      identity_ref: 'test-only-identity-reference',
      catalog: serviceCatalog,
      notification: null,
    },
    digest: 'b'.repeat(64),
    connection_kind: 'rest',
    connection_name: 'Service ticket API',
    environment_id: ids.environment,
    environment_name: 'Local development',
    template_asset: null,
    created_by: ids.admin,
    created_at: date,
    withdrawn_at: null,
    withdrawn_by: null,
    execution_authorized: false,
  }
  const configurations: Configuration[] = options.empty ? [] : [initialConfig]
  const grants: ApproverGrant[] = options.empty
    ? []
    : [
        {
          id: uuid(),
          workspace_id: ids.workspace,
          object_id: ids.reviewer,
          granted_by: ids.admin,
          granted_at: date,
        },
      ]
  let board: BoardView | undefined = scenario
    ? makeBoard(scenario.content.title, scenario, 1, ids.author)
    : undefined
  let previews: Preview[] = []
  let approvals: Approval[] = []

  function makeBoard(
    name: string,
    source: Scenario,
    revision: number,
    createdBy: string,
  ): BoardView {
    return {
      id: ids.board,
      workspace_id: ids.workspace,
      name,
      version: 1,
      scenario_id: source.id,
      revision_version: revision,
      created_by: createdBy,
      created_at: date,
      updated_at: date,
      preparation_status: 'draft',
      approval_status: 'none',
      execution_authorized: false,
      draft: {
        schema_version: 'exercise-preparation-draft/v1',
        name,
        steps: [],
        notification_budget: 0,
        recovery: '',
        window: null,
      },
      scenario: {
        scenario_id: source.id,
        revision_version: revision,
        content: structuredClone(
          revisions.find((item) => item.version === revision)?.content ?? source.content,
        ),
      },
      assets: [],
      contributors: [createdBy],
      latest_preview: null,
      current_approval: null,
      can_edit: true,
      can_approve: false,
      approval_blockers: ['A separate explicit approver is required.'],
    }
  }
  function approvalView(value: Approval): Approval {
    const reasons: string[] = []
    if (value.revoked_at) reasons.push('Decision explicitly revoked.')
    if (Date.parse(value.expires_at) <= Date.now()) reasons.push('Decision expired.')
    if (board?.version !== value.board_version)
      reasons.push('Board version changed; a fresh preview and approval are required.')
    if (!grants.some((grant) => grant.object_id === value.reviewer))
      reasons.push('Approver grant revoked.')
    const preview = previews.find((item) => item.id === value.preview_id)
    if (
      preview?.manifest.configurations.some(
        (bound) => configurations.find((item) => item.id === bound.id)?.withdrawn_at,
      )
    )
      reasons.push('A bound configuration was withdrawn.')
    if (approvals[0]?.id !== value.id) reasons.push('A later decision superseded this decision.')
    return { ...value, validity: { valid: !reasons.length, reasons } }
  }
  function boardView(): BoardView {
    if (!board) throw new Error('Fixture board is absent.')
    const canEdit = role() !== 'viewer'
    const contributor = board.created_by === actor() || board.contributors.includes(actor())
    const granted = grants.some((grant) => grant.object_id === actor())
    const previewUnavailable =
      !connectionAccessible && !!previews[0]?.manifest.configurations.length
    const current = approvals[0] ? approvalView(approvals[0]) : null
    return {
      ...board,
      can_edit: canEdit,
      can_approve: granted && !contributor && !previewUnavailable,
      approval_blockers: [
        ...(!granted ? ['No explicit workspace approver grant.'] : []),
        ...(contributor
          ? ['Creators and preparation contributors cannot review their own work.']
          : []),
        ...(previewUnavailable ? ['preview_reference_unavailable'] : []),
      ],
      latest_preview:
        previews[0] && !previewUnavailable
          ? { ...previews[0], is_current: previews[0].board_version === board.version }
          : null,
      current_approval: current,
      approval_status: current ? (current.validity.valid ? current.decision : 'invalid') : 'none',
    }
  }
  function freeze(): Preview {
    if (!board) throw new Error('No fixture board.')
    const boundIds = new Set(
      (board.draft.steps ?? []).map((step) => step.binding?.configuration_id),
    )
    const bound = configurations
      .filter((item) => boundIds.has(item.id))
      .map((item) => {
        const { withdrawn_at: _at, withdrawn_by: _by, ...snapshot } = item
        return snapshot
      })
    const manifest: Schemas['PreparationManifest'] = {
      schema_version: 'exercise-preparation/v1',
      board_id: board.id,
      workspace_id: board.workspace_id,
      board_version: board.version,
      draft: structuredClone(board.draft),
      scenario: structuredClone(board.scenario),
      assets: structuredClone(board.assets),
      configurations: structuredClone(bound),
      execution_authorized: false,
    }
    const findings: Preview['findings'] = [
      {
        code: 'live_readiness_unverified',
        severity: 'warning',
        message: 'This fixture preview is not live readiness evidence.',
        path: '',
        step_id: null,
      },
      {
        code: 'execution_disabled',
        severity: 'blocker',
        message: 'Execution is disabled. Preparation approval cannot authorize execution.',
        path: '',
        step_id: null,
      },
    ]
    if (!board.draft.window)
      findings.unshift({
        code: 'missing_window',
        severity: 'blocker',
        message: 'An explicit time window is missing.',
        path: '/draft/window',
      })
    if (!board.draft.recovery)
      findings.unshift({
        code: 'missing_recovery',
        severity: 'blocker',
        message: 'Recovery policy is missing.',
        path: '/draft/recovery',
      })
    for (const step of board.draft.steps ?? []) {
      for (const [name, value] of Object.entries(step.parameters ?? {})) {
        if (typeof value === 'object' && value !== null)
          findings.push({
            code: 'result_binding_unverified',
            severity: 'warning',
            message: 'Prior result binding is declarative; its value has not been observed.',
            path: `/draft/steps/${step.id}/parameters/${name}`,
            step_id: step.id,
          })
      }
      if (step.kind === 'operation' && !step.binding)
        findings.unshift({
          code: 'missing_binding',
          severity: 'blocker',
          message: 'Select an operation binding.',
          path: '/draft/steps',
          step_id: step.id,
        })
    }
    const preview: Preview = {
      id: uuid(),
      board_id: board.id,
      board_version: board.version,
      sequence: previews.length + 1,
      digest: digest(manifest),
      manifest,
      findings,
      created_by: actor(),
      created_at: new Date().toISOString(),
      is_current: true,
      execution_authorized: false,
      execution_eligible: false,
    }
    previews = [preview, ...previews]
    board.preparation_status = 'previewed'
    if (!board.contributors.includes(actor())) board.contributors.push(actor())
    return preview
  }
  function addApproval(preview: Preview, input?: Partial<Schemas['PreparationApprovalInput']>) {
    const value: Approval = {
      id: uuid(),
      board_id: ids.board,
      board_version: preview.board_version,
      preview_id: preview.id,
      digest: preview.digest,
      sequence: approvals.length + 1,
      kind: 'preparation',
      execution_authorized: false,
      reviewer: ids.reviewer,
      decision: input?.decision ?? 'approved',
      acknowledge_unverified: true,
      expires_at: input?.expires_at ?? new Date(Date.now() + 86400000).toISOString(),
      note: input?.note ?? 'Explicit fixture review, not live authorization.',
      created_at: date,
      validity: { valid: true, reasons: [] },
      revoked_at: null,
      revoked_by: null,
    }
    approvals = [value, ...approvals]
    return value
  }

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    const root = `/api/workspaces/${ids.workspace}`
    const respond = (data: unknown, status = 200, version?: number) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: version === undefined ? {} : { ETag: `"${version}"` },
        body: status === 204 ? undefined : JSON.stringify(data),
      })
    const match = (version: number) => request.headers()['if-match'] === `"${version}"`
    const conflict = () =>
      respond({ detail: 'The saved version changed; local input was not saved.' }, 409)
    if (path === '/api/me')
      return respond({
        object_id: actor(),
        tenant_id: ids.workspace,
        name: `Explicit fixture ${profile}`,
        organization_admin: profile === 'admin',
      })
    if (path === '/api/workspaces')
      return respond([{ id: ids.workspace, name: 'Service operations', role: role() }])
    if (path === '/api/environments')
      return respond([{ id: ids.environment, name: 'Local development' }])
    if (path === `${root}/assets`) return respond(assets)
    if (path === `${root}/members`)
      return respond([
        { object_id: ids.author, role: 'editor' },
        { object_id: ids.reviewer, role: 'viewer' },
      ])
    if (path === `${root}/connections`) {
      if (method === 'GET') return respond(connectionAccessible ? connections : [])
      const input: Schemas['ConnectionInput'] = request.postDataJSON()
      const value: Connection = {
        id: ids.connection,
        name: input.name,
        kind: input.kind,
        environment_id: input.environment_id,
        scope: input.scope,
        description: input.description ?? '',
        status: 'inventory_only',
      }
      connections.push(value)
      return respond(value, 201)
    }
    if (path.includes('/configurations')) {
      if (!connectionAccessible)
        return respond({ detail: 'Configuration unavailable under current access.' }, 404)
      const connection = connections.find((item) => path.includes(item.id))
      if (!connection) return respond({ detail: 'Connection unavailable.' }, 404)
      if (method === 'GET') {
        if (unavailableConfigurations)
          return respond({ detail: 'Configuration storage unavailable.' }, 503)
        return respond(configurations.filter((item) => item.connection_id === connection.id))
      }
      if (profile !== 'admin')
        return respond({ detail: 'Organization administrator required.' }, 403)
      if (path.endsWith('/withdraw')) {
        const configuration = configurations.find((item) => path.includes(item.id))
        if (!configuration) return respond({ detail: 'Configuration unavailable.' }, 404)
        if (!match(configuration.version)) return conflict()
        configuration.withdrawn_at = new Date().toISOString()
        configuration.withdrawn_by = actor()
        return respond(configuration, 200, configuration.version)
      }
      expect(request.headers()['if-match']).toBeUndefined()
      const content: Schemas['ConnectionConfiguration'] = request.postDataJSON()
      if (connection.kind !== 'sql' && connection.kind !== 'rest' && connection.kind !== 'graph')
        return respond({ detail: 'Unsupported integration.' }, 422)
      const value: Configuration = {
        id: configurations.length ? uuid() : ids.configuration,
        workspace_id: ids.workspace,
        connection_id: connection.id,
        version: configurations.length + 1,
        content,
        digest: digest(content),
        connection_kind: connection.kind,
        connection_name: connection.name,
        environment_id: connection.environment_id,
        environment_name: 'Local development',
        template_asset: content.notification?.template_asset_id
          ? {
              id: ids.template,
              name: assets[0].name,
              media_type: 'text/plain',
              sha256: assets[0].sha256,
              size: 24,
            }
          : null,
        created_by: actor(),
        created_at: date,
        withdrawn_at: null,
        withdrawn_by: null,
        execution_authorized: false,
      }
      configurations.unshift(value)
      return respond(value, 201, value.version)
    }
    if (path.startsWith(`${root}/approvers`)) {
      if (method === 'GET') return respond(grants)
      expect(request.headers()['if-match']).toBeUndefined()
      if (profile !== 'admin')
        return respond({ detail: 'Organization administrator required.' }, 403)
      if (method === 'DELETE') {
        const id = path.split('/').pop()
        const index = grants.findIndex((item) => item.object_id === id)
        if (index >= 0) grants.splice(index, 1)
        return respond(undefined, 204)
      }
      const input: Schemas['ApproverGrantInput'] = request.postDataJSON()
      const grant: ApproverGrant = {
        id: uuid(),
        workspace_id: ids.workspace,
        object_id: input.object_id,
        granted_by: actor(),
        granted_at: date,
      }
      grants.push(grant)
      return respond(grant)
    }
    if (path === `${root}/scenarios`) {
      if (method === 'GET') return respond(scenario ? [scenario] : [])
      const input: { name: string } = request.postDataJSON()
      scenario = {
        id: ids.scenario,
        workspace_id: ids.workspace,
        version: 1,
        updated_at: date,
        content: {
          schema_version: 1,
          title: input.name,
          document: { type: 'doc', content: [{ type: 'paragraph' }] },
          objectives: [],
          nodes: [],
          edges: [],
          asset_ids: [],
        },
      }
      return respond(scenario, 201, 1)
    }
    if (path.endsWith('/planning') || path.endsWith('/comments')) return respond([])
    if (path.endsWith('/revisions')) {
      if (method === 'GET') return respond(revisions)
      if (!scenario || !match(scenario.version)) return conflict()
      const revision: Revision = {
        version: revisions.length + 1,
        actor: actor(),
        created_at: date,
        content: structuredClone(scenario.content),
      }
      revisions = [revision, ...revisions]
      return respond(revision, 201)
    }
    if (path === `${root}/scenarios/${ids.scenario}`) {
      if (!scenario) return respond({ detail: 'Scenario unavailable.' }, 404)
      if (method === 'GET') return respond(scenario, 200, scenario.version)
      if (!match(scenario.version)) return conflict()
      const content: Scenario['content'] = request.postDataJSON()
      scenario = { ...scenario, content, version: scenario.version + 1 }
      return respond(scenario, 200, scenario.version)
    }
    if (path === `${root}/boards`) {
      if (method === 'GET') return respond(board ? [boardView()] : [])
      expect(request.headers()['if-match']).toBeUndefined()
      if (role() === 'viewer') return respond({ detail: 'Editor access required.' }, 403)
      const input: Schemas['BoardCreate'] = request.postDataJSON()
      if (!scenario || !revisions.some((revision) => revision.version === input.revision_version))
        return respond({ detail: 'Published revision unavailable.' }, 422)
      board = makeBoard(input.name, scenario, input.revision_version, actor())
      return respond(boardView(), 201, board.version)
    }
    if (path.startsWith(`${root}/boards/${ids.board}`)) {
      if (options.denied) return respond({ detail: 'Workspace access denied.' }, 403)
      if (!board) return respond({ detail: 'Board unavailable.' }, 404)
      if (path.endsWith('/execute')) {
        executeRequests++
        return respond({ detail: 'Execution permanently disabled.' }, 501)
      }
      if (path.endsWith('/previews')) {
        if (
          method === 'GET' &&
          !connectionAccessible &&
          previews.some((preview) => preview.manifest.configurations.length)
        )
          return respond({ detail: 'Preview configuration references are unavailable.' }, 404)
        if (method === 'GET')
          return respond(
            previews.map((preview) => ({
              ...preview,
              is_current: preview.board_version === board?.version,
            })),
          )
        if (role() === 'viewer') return respond({ detail: 'Editor access required.' }, 403)
        if (!match(board.version)) return conflict()
        return respond(freeze(), 201, board.version)
      }
      if (path.endsWith('/approvals')) {
        if (method === 'GET') return respond(approvals.map(approvalView))
        if (!boardView().can_approve)
          return respond({ detail: 'An independent explicitly granted approver is required.' }, 403)
        if (!match(board.version)) return conflict()
        const input: Schemas['PreparationApprovalInput'] = request.postDataJSON()
        const preview = previews.find((item) => item.id === input.preview_id)
        if (!preview || preview.board_version !== board.version || preview.digest !== input.digest)
          return conflict()
        return respond(addApproval(preview, input), 201, board.version)
      }
      if (path.endsWith('/revoke')) {
        if (!match(board.version)) return conflict()
        const approval = approvals.find((item) => path.includes(item.id))
        if (!approval) return respond({ detail: 'Decision unavailable.' }, 404)
        if (actor() !== approval.reviewer && profile !== 'admin')
          return respond({ detail: 'Only reviewer or administrator can revoke.' }, 403)
        approval.revoked_at = new Date().toISOString()
        approval.revoked_by = actor()
        return respond(approvalView(approval), 200, board.version)
      }
      if (method === 'GET') return respond(boardView(), 200, board.version)
      if (role() === 'viewer') return respond({ detail: 'Editor access required.' }, 403)
      if (failSave)
        return respond({ detail: 'Storage unavailable. No preparation changes were saved.' }, 503)
      if (!match(board.version)) return conflict()
      const input: BoardDraft = request.postDataJSON()
      board = {
        ...board,
        name: input.name,
        draft: input,
        version: board.version + 1,
        preparation_status: 'draft',
      }
      if (!board.contributors.includes(actor())) board.contributors.push(actor())
      return respond(boardView(), 200, board.version)
    }
    throw new Error(`Unhandled preparation fixture: ${method} ${path}`)
  })
  return {
    become(value: Profile) {
      profile = value
    },
    changeBoard() {
      if (board)
        board = {
          ...board,
          version: board.version + 1,
          draft: { ...board.draft, name: 'Another editor’s preparation' },
          name: 'Another editor’s preparation',
        }
    },
    failSaves() {
      failSave = true
    },
    loseConfigurations() {
      unavailableConfigurations = true
    },
    revokeConnectionAccess() {
      connectionAccessible = false
    },
    giveAuthorGrant() {
      grants.push({
        id: uuid(),
        workspace_id: ids.workspace,
        object_id: ids.author,
        granted_by: ids.admin,
        granted_at: date,
      })
    },
    approvedPreview(expiresAt?: string) {
      const preview = freeze()
      return addApproval(preview, expiresAt ? { expires_at: expiresAt } : undefined)
    },
    currentBoard() {
      return boardView()
    },
    configurations() {
      return configurations
    },
    previews() {
      return previews
    },
    approvals() {
      return approvals
    },
    executeRequests() {
      return executeRequests
    },
  }
}
