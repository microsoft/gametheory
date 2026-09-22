import { useId, useRef, useState } from 'react'
import { ErrorNotice } from './api'
import { parseUniqueJson } from './strictJson'

export function CatalogFileInput({
  disabled = false,
  onSelect,
  onClear,
  onFileChange,
}: {
  disabled?: boolean
  onSelect: (content: unknown, filename: string) => void
  onClear: () => void
  onFileChange?: (selected: boolean) => void
}) {
  const hint = useId()
  const sequence = useRef(0)
  const [reading, setReading] = useState(false)
  const [error, setError] = useState<unknown>()
  return (
    <div>
      <label>
        Operation catalog JSON file
        <input
          type="file"
          accept=".json,application/json"
          disabled={disabled}
          aria-describedby={hint}
          onChange={(event) => {
            const file = event.target.files?.[0]
            const request = ++sequence.current
            setError(undefined)
            onClear()
            onFileChange?.(!!file)
            if (!file) {
              setReading(false)
              return
            }
            if (file.size > 512 * 1024) {
              setReading(false)
              setError(new Error('Choose an operation catalog no larger than 512 KiB.'))
              return
            }
            setReading(true)
            void file
              .text()
              .then((text) => {
                if (sequence.current === request) onSelect(parseUniqueJson(text), file.name)
              })
              .catch((cause: unknown) => {
                if (sequence.current === request) setError(cause)
              })
              .finally(() => {
                if (sequence.current === request) setReading(false)
              })
          }}
        />
      </label>
      <p id={hint} className="preparation-note">
        Select a local operation-catalog/v1 file. Inspect its operations before registering. Remote
        references, credentials, raw SQL, scripts, and unrestricted HTTP are not accepted. The file
        is not an exercise installer and does not contact a target.
      </p>
      {reading && <p role="status">Reading the selected catalog...</p>}
      <ErrorNotice error={error} />
    </div>
  )
}
