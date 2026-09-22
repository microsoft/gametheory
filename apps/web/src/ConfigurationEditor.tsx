import { useMemo, useState } from 'react'
import type { components } from './api.generated'
import type { Asset } from './types'
import { ErrorNotice } from './api'
import { readOperationCatalog, CatalogInspection } from './Catalog'
import { CatalogFileInput } from './CatalogFileInput'
import { ConflictNotice, exportJson, UnsavedChanges } from './PreparationShared'
import { configurationErrors } from './configurationValidation'

export type ConfigurationContent = components['schemas']['ConnectionConfiguration']

export function emptyConfiguration(kind: string): ConfigurationContent {
  return {
    schema_version: 'connection-configuration/v1',
    classification: 'unknown',
    resource_id: '',
    endpoint: '',
    database: '',
    identity_ref: '',
    catalog: { schema_version: 'operation-catalog/v1', name: 'Unconfigured', operations: [] },
    notification:
      kind === 'graph'
        ? { template_asset_id: null, sender: '', recipients: [], trusted_link: '' }
        : null,
  }
}

export function ConfigurationEditor({
  kind,
  assets,
  initial,
  onRegister,
  onReload,
  disabled = false,
}: {
  kind: string
  assets: Asset[]
  initial?: ConfigurationContent
  onRegister: (content: ConfigurationContent) => Promise<void>
  onReload: () => Promise<void>
  disabled?: boolean
}) {
  const defaults = useMemo(() => emptyConfiguration(kind), [kind])
  const [draft, setDraft] = useState<ConfigurationContent>(() => initial ?? defaults)
  const [filename, setFilename] = useState(initial ? 'Existing immutable catalog' : '')
  const [recipients, setRecipients] = useState(initial?.notification?.recipients?.join('\n') ?? '')
  const [reviewed, setReviewed] = useState(false)
  const [error, setError] = useState<unknown>()
  const [pending, setPending] = useState(false)
  const [notice, setNotice] = useState('')
  const [fileDirty, setFileDirty] = useState(false)
  const [filePickerVersion, setFilePickerVersion] = useState(0)
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(defaults) || !!filename || reviewed || fileDirty
  const inputErrors = configurationErrors(draft, kind)
  const exportInput = () => exportJson(draft, 'connection-configuration-input.json')
  function change(value: ConfigurationContent) {
    setDraft(value)
    setReviewed(false)
    setNotice('')
  }
  async function register() {
    if (inputErrors.length) return
    setPending(true)
    setError(undefined)
    try {
      await onRegister(draft)
      setDraft(defaults)
      setFilename('')
      setRecipients('')
      setReviewed(false)
      setFileDirty(false)
      setFilePickerVersion((value) => value + 1)
      setNotice('Configuration registered. Connectivity and target permissions remain unverified.')
    } catch (cause) {
      setError(cause)
    } finally {
      setPending(false)
    }
  }
  return (
    <section className="glass preparation-panel">
      <h2>Register a configuration revision</h2>
      <p className="preparation-note">
        Each registration preserves immutable target metadata and an operation catalog. It neither
        activates a connector nor grants target access. Leave unresolved values blank rather than
        entering placeholders.
      </p>
      <ErrorNotice error={error} />
      {inputErrors.length > 0 && (
        <div className="notice error" role="alert">
          <strong>Correct the configuration metadata</strong>
          <ul>
            {inputErrors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </div>
      )}
      <ConflictNotice
        error={error}
        pending={pending}
        onExport={exportInput}
        onReload={() => {
          setPending(true)
          void onReload()
            .then(() => {
              setDraft(defaults)
              setFilename('')
              setRecipients('')
              setReviewed(false)
              setFileDirty(false)
              setFilePickerVersion((value) => value + 1)
              setError(undefined)
            })
            .catch(setError)
            .finally(() => setPending(false))
        }}
      />
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (reviewed && filename) void register()
        }}
      >
        <fieldset className="preparation-inputs" disabled={pending || disabled}>
          <legend>Administrator-controlled target metadata</legend>
          <div className="preparation-fields">
            <label>
              Target classification
              <select
                value={draft.classification}
                onChange={(event) => {
                  const classification = event.target.value
                  if (
                    classification === 'unknown' ||
                    classification === 'nonproduction' ||
                    classification === 'production'
                  )
                    change({ ...draft, classification })
                }}
              >
                <option value="unknown">Unknown — not classified</option>
                <option value="nonproduction">Nonproduction</option>
                <option value="production">Production — execution ineligible</option>
              </select>
            </label>
            <label>
              Concrete resource identity
              <input
                value={draft.resource_id}
                maxLength={512}
                onChange={(event) => change({ ...draft, resource_id: event.target.value })}
              />
            </label>
            <label>
              {kind === 'sql' ? 'SQL server hostname' : 'Endpoint metadata'}
              <input
                value={draft.endpoint}
                maxLength={1000}
                type={kind === 'rest' ? 'url' : 'text'}
                onChange={(event) => change({ ...draft, endpoint: event.target.value })}
                aria-describedby="endpoint-guidance"
              />
              <span id="endpoint-guidance" className="preparation-note">
                {kind === 'sql'
                  ? 'Hostname only, not a connection string or query.'
                  : 'No embedded credentials, secret query parameters, or arbitrary request headers.'}
              </span>
            </label>
            {kind === 'sql' && (
              <label>
                Database metadata
                <input
                  value={draft.database}
                  maxLength={128}
                  onChange={(event) => change({ ...draft, database: event.target.value })}
                />
              </label>
            )}
            <label className="full">
              Identity reference (no credentials)
              <input
                value={draft.identity_ref}
                maxLength={512}
                onChange={(event) => change({ ...draft, identity_ref: event.target.value })}
              />
              <span className="preparation-note">
                Reference the intended managed identity or service principal. Do not paste
                passwords, access tokens, secrets, connection strings, or authorization headers.
              </span>
            </label>
          </div>
          {draft.classification !== 'nonproduction' && (
            <p className="notice">
              {draft.classification === 'production'
                ? 'Production targets are ineligible for execution.'
                : 'Unclassified targets are ineligible for execution.'}{' '}
              Nonproduction classification alone would not establish readiness.
            </p>
          )}
          {kind === 'graph' && draft.notification && (
            <fieldset className="preparation-fields">
              <legend>Fixed notification policy</legend>
              <label className="full">
                Immutable template asset version
                <select
                  value={draft.notification.template_asset_id ?? ''}
                  onChange={(event) =>
                    change({
                      ...draft,
                      notification: {
                        ...draft.notification!,
                        template_asset_id: event.target.value || null,
                      },
                    })
                  }
                >
                  <option value="">Unresolved — select a ready workspace asset</option>
                  {assets
                    .filter((asset) => asset.state === 'ready')
                    .map((asset) => (
                      <option key={asset.id} value={asset.id}>
                        {asset.name} · {asset.id}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Fixed sender mailbox
                <input
                  type="email"
                  maxLength={254}
                  value={draft.notification.sender}
                  onChange={(event) =>
                    change({
                      ...draft,
                      notification: { ...draft.notification!, sender: event.target.value },
                    })
                  }
                />
              </label>
              <label>
                Trusted operational-app link
                <input
                  type="url"
                  maxLength={1000}
                  value={draft.notification.trusted_link}
                  onChange={(event) =>
                    change({
                      ...draft,
                      notification: { ...draft.notification!, trusted_link: event.target.value },
                    })
                  }
                />
              </label>
              <label className="full">
                Explicit recipient mailboxes
                <textarea
                  maxLength={25600}
                  value={recipients}
                  onChange={(event) => {
                    setRecipients(event.target.value)
                    change({
                      ...draft,
                      notification: {
                        ...draft.notification!,
                        recipients: event.target.value
                          .split(/[\n,;]+/)
                          .map((value) => value.trim())
                          .filter(Boolean),
                      },
                    })
                  }}
                />
                <span className="preparation-note">
                  One mailbox per line. No wildcard audience, To/CC/BCC expressions, attachments, or
                  operation-selected recipients. Sending and delivery remain unverified and
                  disabled.
                </span>
              </label>
            </fieldset>
          )}
          <CatalogFileInput
            key={filePickerVersion}
            disabled={pending || disabled}
            onFileChange={setFileDirty}
            onClear={() => {
              setFilename('')
              setReviewed(false)
              setError(undefined)
              setDraft((previous) => ({ ...previous, catalog: defaults.catalog }))
            }}
            onSelect={(value, name) => {
              const catalog = readOperationCatalog(value, kind)
              setDraft((previous) => ({ ...previous, catalog }))
              setReviewed(false)
              setNotice('')
              setFilename(name)
              setError(undefined)
            }}
          />
          {filename && (
            <div className="preparation-section">
              <p className="preparation-note">Selected file: {filename}</p>
              <CatalogInspection catalog={draft.catalog} />
              <label className="check">
                <input
                  type="checkbox"
                  checked={reviewed}
                  onChange={(event) => setReviewed(event.target.checked)}
                />
                I inspected the catalog and target metadata. Registration does not verify or
                activate this target.
              </label>
            </div>
          )}
        </fieldset>
        <div className="preparation-actions">
          <button
            className="primary"
            disabled={pending || disabled || !reviewed || !filename || inputErrors.length > 0}
          >
            {pending ? 'Registering...' : 'Register configuration'}
          </button>
          <button type="button" onClick={exportInput}>
            Export configuration input
          </button>
        </div>
      </form>
      <UnsavedChanges dirty={dirty || pending} onExport={exportInput} />
    </section>
  )
}
