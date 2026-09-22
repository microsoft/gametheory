import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { Node, mergeAttributes } from '@tiptap/core'
import {
  EditorContent,
  NodeViewWrapper,
  ReactNodeViewRenderer,
  useEditor,
  type NodeViewProps,
} from '@tiptap/react'
import { useQuery } from '@tanstack/react-query'
import StarterKit from '@tiptap/starter-kit'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import TurndownService from 'turndown'
import { download, ErrorNotice, useSession } from './api'
import type { Asset, Content } from './types'

const EmbedContext = createContext<{
  wid: string
  content: Content
  assets: Asset[]
  open: (kind: string, target: string) => void
} | null>(null)

function ReferenceView({ node }: NodeViewProps) {
  const context = useContext(EmbedContext)
  const { api } = useSession()
  const kind = typeof node.attrs.kind === 'string' ? node.attrs.kind : ''
  const target = typeof node.attrs.targetId === 'string' ? node.attrs.targetId : ''
  const asset = context?.assets.find((item) => item.id === target)
  const marker = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [url, setUrl] = useState('')
  const [imageFailed, setImageFailed] = useState(false)
  useEffect(() => {
    if (!marker.current) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: '250px' },
    )
    observer.observe(marker.current)
    return () => observer.disconnect()
  }, [])
  const image = useQuery({
    queryKey: [context?.wid, target, 'asset-content'],
    enabled:
      visible &&
      !!context &&
      kind === 'asset' &&
      asset?.state === 'ready' &&
      asset.media_type.startsWith('image/'),
    queryFn: () => api.blob(`/workspaces/${context!.wid}/assets/${target}/content`),
    staleTime: Infinity,
  })
  useEffect(() => {
    if (!image.data) {
      setUrl('')
      setImageFailed(false)
      return
    }
    const next = URL.createObjectURL(image.data)
    setUrl(next)
    setImageFailed(false)
    return () => URL.revokeObjectURL(next)
  }, [image.data])
  return (
    <NodeViewWrapper className="reference-card" contentEditable={false}>
      <div ref={marker}>
        <button className="embed-heading" onClick={() => context?.open(kind, target)}>
          {kind === 'flow'
            ? 'Scenario flow / Open focused editor'
            : (asset?.name ?? `Inspect ${kind} ${target}`)}
        </button>
        {kind === 'flow' && (
          <div className="mini-flow">
            {context?.content.nodes.slice(0, 4).map((step) => (
              <span key={step.id}>{step.label}</span>
            ))}
          </div>
        )}
        {kind === 'asset' && asset && (
          <small>
            {asset.media_type} · Immutable version {asset.id.slice(0, 8)}
          </small>
        )}
        {url && !imageFailed && (
          <img
            className="embedded-image"
            src={url}
            alt={asset?.name ?? 'Scenario asset'}
            onError={() => setImageFailed(true)}
          />
        )}
        {imageFailed && (
          <p className="error-text">
            This image cannot be decoded. Inspect or download the original asset.
          </p>
        )}
        <ErrorNotice error={image.error} />
      </div>
    </NodeViewWrapper>
  )
}

const Reference = Node.create({
  name: 'reference',
  group: 'block',
  atom: true,
  selectable: true,
  addAttributes() {
    return { kind: { default: 'flow' }, targetId: { default: 'scenario' } }
  },
  parseHTML() {
    return [{ tag: 'div[data-reference-kind]' }]
  },
  addNodeView() {
    return ReactNodeViewRenderer(ReferenceView)
  },
  renderHTML({ node, HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(HTMLAttributes, {
        'data-reference-kind': node.attrs.kind,
        'data-target-id': node.attrs.targetId,
        class: 'reference-card',
        role: 'button',
        tabindex: '0',
      }),
      node.attrs.kind === 'flow'
        ? 'Scenario flow  /  Open focused editor'
        : `Versioned ${node.attrs.kind}  /  ${node.attrs.targetId}`,
    ]
  },
})

export function DocumentEditor({
  content,
  editable,
  onChange,
  onReference,
  wid,
  assets,
}: {
  content: Content
  editable: boolean
  onChange: (document: Content['document']) => void
  onReference: (kind: string, target: string) => void
  wid: string
  assets: Asset[]
}) {
  const [error, setError] = useState<unknown>()
  const markdownInput = useRef<HTMLInputElement>(null)
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ link: { openOnClick: false, autolink: false } }),
      Reference,
    ],
    content: content.document,
    editable,
    editorProps: {
      attributes: { 'aria-label': 'Scenario document', role: 'textbox', 'aria-multiline': 'true' },
    },
    onUpdate: ({ editor: current }) => onChangeRef.current(current.getJSON()),
  })
  useEffect(() => {
    if (editor && JSON.stringify(editor.getJSON()) !== JSON.stringify(content.document)) {
      editor.commands.setContent(content.document, { emitUpdate: false })
    }
  }, [content.document, editor])
  useEffect(() => {
    editor?.setEditable(editable)
  }, [editable, editor])
  if (!editor) return <p>Loading document editor...</p>
  const addReference = (kind: string, targetId: string) =>
    editor.chain().focus().insertContent({ type: 'reference', attrs: { kind, targetId } }).run()
  async function importMarkdown(file: File) {
    try {
      if (file.size > 1024 * 1024) throw new Error('Markdown import is limited to 1 MB.')
      const markdown = await file.text()
      const tokens = marked.lexer(markdown)
      marked.walkTokens(tokens, (token) => {
        if (['html', 'image', 'table'].includes(token.type))
          throw new Error(
            'Raw HTML, images, and tables are not supported by Markdown import. Upload images as assets and embed them instead.',
          )
        if (token.type === 'link' && !/^(https:\/\/|mailto:)/.test(token.href))
          throw new Error('Links must use HTTPS or mailto.')
      })
      const html = DOMPurify.sanitize(await marked.parse(markdown))
      if (
        window.confirm(
          'Replace this document narrative? The flow and asset library will be retained.',
        )
      ) {
        editor?.commands.setContent(html, { emitUpdate: true })
      }
      setError(undefined)
    } catch (cause) {
      setError(cause)
    }
  }
  function exportMarkdown() {
    const service = new TurndownService({ headingStyle: 'atx' })
    service.addRule('references', {
      filter: (element) => element.hasAttribute('data-reference-kind'),
      replacement: (_text, element) =>
        `\n\n> Game Theory ${element.getAttribute('data-reference-kind')} reference: ${element.getAttribute('data-target-id')}\n\n`,
    })
    download(
      new Blob([service.turndown(editor!.getHTML())], { type: 'text/markdown' }),
      'scenario.md',
    )
  }
  const openReference = (target: EventTarget) => {
    if (!(target instanceof Element)) return
    const reference = target.closest<HTMLElement>('[data-reference-kind]')
    if (reference)
      onReference(reference.dataset.referenceKind ?? '', reference.dataset.targetId ?? '')
  }
  return (
    <div className="document-editor">
      <div className="editor-toolbar" aria-label="Document formatting">
        <button
          disabled={!editable}
          aria-label="Bold"
          aria-pressed={editor.isActive('bold')}
          onClick={() => editor.chain().focus().toggleBold().run()}
        >
          <strong>B</strong>
        </button>
        <button
          disabled={!editable}
          aria-label="Italic"
          aria-pressed={editor.isActive('italic')}
          onClick={() => editor.chain().focus().toggleItalic().run()}
        >
          <em>I</em>
        </button>
        <button
          disabled={!editable}
          onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        >
          Heading
        </button>
        <button
          disabled={!editable}
          onClick={() => editor.chain().focus().toggleBulletList().run()}
        >
          List
        </button>
        <button disabled={!editable} onClick={() => addReference('flow', 'scenario')}>
          Embed flow
        </button>
        <select
          disabled={!editable || !content.asset_ids.length}
          aria-label="Embed asset"
          value=""
          onChange={(e) => {
            if (e.target.value) addReference('asset', e.target.value)
          }}
        >
          <option value="">Embed asset...</option>
          {content.asset_ids.map((id) => (
            <option key={id} value={id}>
              {assets.find((asset) => asset.id === id)?.name ?? id}
            </option>
          ))}
        </select>
        <button disabled={!editable} onClick={() => markdownInput.current?.click()}>
          Import Markdown
        </button>
        <input
          ref={markdownInput}
          hidden
          type="file"
          accept=".md,.txt"
          disabled={!editable}
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void importMarkdown(file)
            e.target.value = ''
          }}
        />
        <button onClick={exportMarkdown}>Export Markdown</button>
      </div>
      <ErrorNotice error={error} />
      <EmbedContext.Provider value={{ wid, content, assets, open: onReference }}>
        <div
          onClick={(e) => openReference(e.target)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') openReference(e.target)
          }}
        >
          <EditorContent editor={editor} />
        </div>
      </EmbedContext.Provider>
      <p className="editor-footnote">
        Markdown exports narrative with explanatory reference labels. Export scenario JSON to
        preserve the complete graph and asset references.
      </p>
    </div>
  )
}
