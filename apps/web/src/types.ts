import type { components } from './api.generated'

type Schemas = components['schemas']
export type Content = Required<Schemas['ScenarioContent']>
export type Scenario = Omit<Schemas['ScenarioView'], 'content'> & { content: Content }
export type Workspace = Schemas['WorkspaceView']
export type FlowNode = Schemas['FlowNode']
export type FlowEdge = Schemas['FlowEdge']
export interface Config {
  cloud: string
  auth: { configured: boolean; client_id: string; authority: string; scope: string }
  capabilities: {
    authoring: boolean
    assets: boolean
    planning: boolean
    execution: boolean
    run_assistant: boolean
  }
  max_upload_bytes: number
}
export interface Environment {
  id: string
  name: string
}
export interface Connection {
  id: string
  name: string
  kind: string
  scope: string
  environment_id: string
  description: string
  status: 'inventory_only'
}
export interface Asset {
  id: string
  name: string
  media_type: string
  sha256: string
  size: number
  state: 'staged' | 'ready' | 'cleanup_pending' | 'abandoned'
  previous_id: string | null
  actor: string
  created_at: string
}
export interface Proposal {
  summary: string
  content: Content
}
export interface Planning {
  id: string
  prompt: string
  actor: string
  base_version: number
  status: 'queued' | 'running' | 'proposed' | 'applied' | 'rejected' | 'failed'
  error: string | null
  created_at: string
  proposal: Proposal | null
}
export interface Comment {
  id: string
  body: string
  actor: string
  base_version: number
  created_at: string
}
export interface Revision {
  version: number
  actor: string
  created_at: string
  content: Content
}
export interface Member {
  object_id: string
  role: Workspace['role']
}

export function normalizeScenario(raw: Schemas['ScenarioView']): Scenario {
  return {
    ...raw,
    content: {
      ...raw.content,
      schema_version: 1,
      document: raw.content.document ?? { type: 'doc', content: [{ type: 'paragraph' }] },
      objectives: raw.content.objectives ?? [],
      nodes: raw.content.nodes ?? [],
      edges: raw.content.edges ?? [],
      asset_ids: raw.content.asset_ids ?? [],
    },
  }
}
