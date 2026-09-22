import { useState } from 'react'
import {
  Background,
  Controls,
  ReactFlow,
  type Node,
  type Edge,
  type OnNodesChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { Connection, Content, Environment, FlowNode } from './types'

export function makeMermaid(content: Content) {
  const label = (value: string) =>
    Array.from(value)
      .map((c) => (/[\p{L}\p{N} .,:?!_\-/]/u.test(c) ? c : `#${c.codePointAt(0)};`))
      .join('')
  const id = (value: string) => `n${value.replaceAll('-', '')}`
  return [
    'flowchart TD',
    ...content.nodes.map((node) => `  ${id(node.id)}["${label(node.label)}"]`),
    ...content.edges.map(
      (edge) =>
        `  ${id(edge.source)} -->${edge.label ? `|"${label(edge.label)}"|` : ''} ${id(edge.target)}`,
    ),
  ].join('\n')
}

export function FlowEditor({
  content,
  editable,
  connections,
  environments,
  onChange,
}: {
  content: Content
  editable: boolean
  connections: Connection[]
  environments: Environment[]
  onChange: (content: Content) => void
}) {
  const [selected, setSelected] = useState<string>()
  const [selectedEdge, setSelectedEdge] = useState<string>()
  const nodes: Node[] = content.nodes.map((node) => ({
    id: node.id,
    position: node.position,
    data: {
      label: (
        <>
          <small>{node.kind.toUpperCase()}</small>
          <strong>{node.label}</strong>
          <span>
            {environments.find((e) => e.id === node.environment_id)?.name ?? 'Target not bound'}
          </span>
        </>
      ),
    },
    selected: node.id === selected,
  }))
  const edges: Edge[] = content.edges.map((edge) => ({
    ...edge,
    selected: edge.id === selectedEdge,
  }))
  const node = content.nodes.find((item) => item.id === selected)
  const edge = content.edges.find((item) => item.id === selectedEdge)
  const changeNodes: OnNodesChange = (changes) => {
    if (!editable) return
    const positions = changes.filter((change) => change.type === 'position')
    if (positions.length)
      onChange({
        ...content,
        nodes: content.nodes.map((n) => {
          const change = positions.find((p) => p.id === n.id)
          return change?.position ? { ...n, position: change.position } : n
        }),
      })
  }
  function updateNode(change: Partial<FlowNode>) {
    onChange({
      ...content,
      nodes: content.nodes.map((n) => (n.id === selected ? { ...n, ...change } : n)),
    })
  }
  return (
    <div className="flow-panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">FOCUSED EDITOR</span>
          <h2>Scenario flow</h2>
        </div>
        <button
          disabled={!editable}
          onClick={() => {
            const id = crypto.randomUUID()
            onChange({
              ...content,
              nodes: [
                ...content.nodes,
                {
                  id,
                  kind: 'action',
                  label: 'New step',
                  detail: '',
                  position: {
                    x: 60 + (content.nodes.length % 3) * 240,
                    y: 60 + Math.floor(content.nodes.length / 3) * 160,
                  },
                  connection_id: null,
                  environment_id: null,
                },
              ],
            })
            setSelected(id)
            setSelectedEdge(undefined)
          }}
        >
          Add step
        </button>
      </div>
      <p className="muted">
        Connect handles to define a branch. These are planning definitions, not executable
        approvals.
      </p>
      <div className="flow-canvas" aria-label="Scenario flow canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={changeNodes}
          nodesDraggable={editable}
          nodesConnectable={editable}
          deleteKeyCode={null}
          fitView
          onNodeClick={(_event, n) => {
            setSelected(n.id)
            setSelectedEdge(undefined)
          }}
          onEdgeClick={(_event, e) => {
            setSelectedEdge(e.id)
            setSelected(undefined)
          }}
          onConnect={(connection) => {
            if (editable)
              onChange({
                ...content,
                edges: [
                  ...content.edges,
                  {
                    id: crypto.randomUUID(),
                    source: connection.source,
                    target: connection.target,
                    label: '',
                  },
                ],
              })
          }}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      {node && (
        <fieldset disabled={!editable} className="inspector form-grid">
          <legend>Selected step</legend>
          <label>
            Label
            <input
              value={node.label}
              maxLength={160}
              onChange={(e) => updateNode({ label: e.target.value })}
            />
          </label>
          <label>
            Type
            <select
              value={node.kind}
              onChange={(e) => updateNode({ kind: e.target.value as FlowNode['kind'] })}
            >
              <option value="action">Action</option>
              <option value="condition">Condition</option>
              <option value="wait">Wait</option>
              <option value="approval">Approval</option>
            </select>
          </label>
          <label className="full">
            Intent / condition
            <textarea
              value={node.detail ?? ''}
              maxLength={4000}
              onChange={(e) => updateNode({ detail: e.target.value })}
            />
          </label>
          <label>
            Connection
            <select
              value={node.connection_id ?? ''}
              onChange={(e) => {
                const connection = connections.find((c) => c.id === e.target.value)
                updateNode({
                  connection_id: connection?.id ?? null,
                  environment_id: connection?.environment_id ?? null,
                })
              }}
            >
              <option value="">Not bound</option>
              {connections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Environment
            <select
              disabled={!!node.connection_id}
              value={node.environment_id ?? ''}
              onChange={(e) => updateNode({ environment_id: e.target.value || null })}
            >
              <option value="">Not bound</option>
              {environments.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              if (JSON.stringify(content.document).includes(node.id)) {
                window.alert('Remove this step’s document reference before deleting it.')
                return
              }
              onChange({
                ...content,
                nodes: content.nodes.filter((n) => n.id !== node.id),
                edges: content.edges.filter((e) => e.source !== node.id && e.target !== node.id),
              })
              setSelected(undefined)
            }}
          >
            Remove step
          </button>
        </fieldset>
      )}
      {edge && (
        <fieldset disabled={!editable} className="inspector">
          <legend>Selected branch</legend>
          <label>
            Branch label
            <input
              value={edge.label ?? ''}
              maxLength={160}
              onChange={(e) =>
                onChange({
                  ...content,
                  edges: content.edges.map((item) =>
                    item.id === edge.id ? { ...item, label: e.target.value } : item,
                  ),
                })
              }
            />
          </label>
          <button
            onClick={() => {
              onChange({ ...content, edges: content.edges.filter((e) => e.id !== edge.id) })
              setSelectedEdge(undefined)
            }}
          >
            Remove branch
          </button>
        </fieldset>
      )}
      <details>
        <summary>Generated Mermaid source</summary>
        <pre data-testid="mermaid-source">{makeMermaid(content)}</pre>
      </details>
    </div>
  )
}
