import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { download, ErrorNotice, useSession } from './api'
import type { Asset, Content } from './types'

export function AssetPanel({
  wid,
  assets,
  content,
  editable,
  selected,
  onSelect,
  onChange,
  onUploaded,
}: {
  wid: string
  assets: Asset[]
  content: Content
  editable: boolean
  selected?: string
  onSelect: (id: string | undefined) => void
  onChange: (content: Content) => void
  onUploaded: (id: string) => void
}) {
  const { api, config } = useSession()
  const cache = useQueryClient()
  const [previous, setPrevious] = useState('')
  const [file, setFile] = useState<File>()
  const [previewUrl, setPreviewUrl] = useState('')
  const [text, setText] = useState('')
  const [error, setError] = useState<unknown>()
  const asset = assets.find((a) => a.id === selected)
  const preview = useQuery({
    queryKey: [wid, selected, 'asset-content'],
    enabled: !!asset && asset.state === 'ready',
    queryFn: () => api.blob(`/workspaces/${wid}/assets/${selected}/content`),
    staleTime: Infinity,
  })
  useEffect(() => {
    if (!preview.data) {
      setPreviewUrl('')
      setText('')
      return
    }
    let active = true
    const url = URL.createObjectURL(preview.data)
    setPreviewUrl(url)
    if (asset?.media_type.startsWith('text/') || asset?.media_type === 'application/json') {
      void preview.data
        .text()
        .then((value) => {
          if (active) setText(value)
        })
        .catch((cause) => {
          if (active) setError(cause)
        })
    } else setText('')
    return () => {
      active = false
      URL.revokeObjectURL(url)
    }
  }, [preview.data, asset?.media_type])
  const upload = useMutation({
    mutationKey: [wid, 'upload'],
    mutationFn: () => {
      if (!file) throw new Error('Choose an asset file')
      if (file.size > config.max_upload_bytes)
        throw new Error('File exceeds the configured upload limit')
      const extensions: Record<string, string> = {
        md: 'text/markdown',
        csv: 'text/csv',
        json: 'application/json',
        txt: 'text/plain',
      }
      const media = extensions[file.name.split('.').pop()?.toLowerCase() ?? ''] ?? file.type
      const form = new FormData()
      form.append('file', new File([file], file.name, { type: media }))
      return api.send<Asset>(
        `/workspaces/${wid}/assets${previous ? `?previous_id=${encodeURIComponent(previous)}` : ''}`,
        'POST',
        form,
      )
    },
    onSuccess: (value) => {
      void cache.invalidateQueries({ queryKey: [wid, 'assets'] })
      onUploaded(value.id)
      onSelect(value.id)
      setFile(undefined)
    },
    onError: () => {
      void cache.invalidateQueries({ queryKey: [wid, 'assets'] })
    },
  })
  return (
    <section className="stack">
      <span className="eyebrow">SCENARIO MATERIAL</span>
      <h2>Versioned assets</h2>
      <p>
        Upload images, PDF documents, text, Markdown, CSV, or JSON. Each upload is immutable; a new
        version does not change existing scenario references.
      </p>
      {!config.capabilities.assets && (
        <div className="notice">
          Blob Storage is not configured. Upload and preview are unavailable.
        </div>
      )}
      <ErrorNotice error={error ?? upload.error ?? preview.error} />
      <form
        className="asset-upload"
        onSubmit={(e) => {
          e.preventDefault()
          upload.mutate()
        }}
      >
        <label>
          Version of
          <select
            disabled={!editable || upload.isPending}
            value={previous}
            onChange={(e) => setPrevious(e.target.value)}
          >
            <option value="">New asset</option>
            {assets
              .filter((a) => a.state === 'ready')
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.id.slice(0, 8)}
                </option>
              ))}
          </select>
        </label>
        <label>
          File (up to {Math.floor(config.max_upload_bytes / 1024 / 1024)} MB)
          <input
            key={upload.isSuccess && !file ? 'empty' : 'file'}
            type="file"
            accept=".png,.jpg,.jpeg,.webp,.pdf,.md,.txt,.csv,.json"
            disabled={!editable || upload.isPending}
            onChange={(e) => setFile(e.target.files?.[0])}
          />
        </label>
        <button className="primary" disabled={!editable || !file || upload.isPending}>
          {upload.isPending ? 'Uploading...' : 'Upload and reference'}
        </button>
      </form>
      <div className="asset-list">
        {assets.map((item) => (
          <div key={item.id} className={`asset-row ${item.id === selected ? 'selected' : ''}`}>
            <button className="asset-name" onClick={() => onSelect(item.id)}>
              <strong>{item.name}</strong>
              <small>
                {item.media_type} · {item.id.slice(0, 8)} · {item.state}
              </small>
            </button>
            <label className="check">
              <input
                type="checkbox"
                disabled={!editable || item.state !== 'ready'}
                checked={content.asset_ids.includes(item.id)}
                onChange={(e) => {
                  if (!e.target.checked && JSON.stringify(content.document).includes(item.id)) {
                    setError(
                      new Error(
                        'Remove this asset’s document embed before removing its scenario reference.',
                      ),
                    )
                    return
                  }
                  onChange({
                    ...content,
                    asset_ids: e.target.checked
                      ? [...content.asset_ids, item.id]
                      : content.asset_ids.filter((id) => id !== item.id),
                  })
                  setError(undefined)
                }}
              />
              In plan
            </label>
          </div>
        ))}
      </div>
      {asset && (
        <article className="asset-inspector">
          <h3>{asset.name}</h3>
          <dl>
            <dt>Immutable version ID</dt>
            <dd>
              <code>{asset.id}</code>
            </dd>
            <dt>SHA-256</dt>
            <dd>
              <code>{asset.sha256}</code>
            </dd>
            <dt>Provenance</dt>
            <dd>
              Uploaded by {asset.actor} · {new Date(asset.created_at).toLocaleString()}
            </dd>
            <dt>Previous version</dt>
            <dd>{asset.previous_id ?? 'Original upload'}</dd>
            <dt>Usage</dt>
            <dd>
              {content.asset_ids.includes(asset.id)
                ? 'Referenced by this draft'
                : 'Available in workspace; not referenced by this draft'}
            </dd>
          </dl>
          {asset.state !== 'ready' && (
            <div className="notice error">
              This upload has not completed and cannot be used. An operator can inspect and clean up
              staged uploads.
            </div>
          )}
          {preview.isFetching && <p role="status">Loading asset...</p>}
          {previewUrl && asset.media_type.startsWith('image/') && (
            <img className="asset-image" src={previewUrl} alt={asset.name} />
          )}
          {text && <pre>{text}</pre>}
          {preview.data && (
            <button onClick={() => download(preview.data!, asset.name)}>Download original</button>
          )}
        </article>
      )}
    </section>
  )
}
