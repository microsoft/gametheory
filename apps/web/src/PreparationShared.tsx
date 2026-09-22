import { useEffect, useId, useRef } from 'react'
import { useBlocker } from 'react-router-dom'
import { ApiError, download } from './api'

export function exportJson(value: unknown, name: string) {
  download(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }), name)
}

export function isConflict(error: unknown) {
  return error instanceof ApiError && (error.status === 409 || error.status === 412)
}

export function ConflictNotice({
  error,
  onExport,
  onReload,
  pending = false,
}: {
  error: unknown
  onExport: () => void
  onReload: () => void
  pending?: boolean
}) {
  if (!isConflict(error)) return null
  return (
    <div className="notice" role="status">
      <strong>The saved version changed.</strong> Your input has not been discarded. Export it
      before reloading, then review the latest version before trying again.
      <div className="toolbar">
        <button type="button" onClick={onExport}>
          Export my input
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => {
            if (window.confirm('Discard local input and reload the latest saved version?'))
              onReload()
          }}
        >
          Reload latest version
        </button>
      </div>
    </div>
  )
}

export function UnsavedChanges({ dirty, onExport }: { dirty: boolean; onExport: () => void }) {
  const heading = useId()
  const dialog = useRef<HTMLDialogElement>(null)
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      (currentLocation.pathname !== nextLocation.pathname ||
        currentLocation.search !== nextLocation.search),
  )
  useEffect(() => {
    if (blocker.state === 'blocked' && !dialog.current?.open) dialog.current?.showModal()
  }, [blocker.state])
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (!dirty) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])
  if (blocker.state !== 'blocked') return null
  return (
    <dialog
      ref={dialog}
      className="glass modal"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={heading}
      onCancel={(event) => {
        event.preventDefault()
        blocker.reset()
      }}
    >
      <h2 id={heading}>Leave unsaved work?</h2>
      <p>Your unsaved input will be discarded. You can export a copy before leaving.</p>
      <div className="toolbar">
        <button autoFocus type="button" onClick={() => blocker.reset()}>
          Keep editing
        </button>
        <button type="button" onClick={onExport}>
          Export my input
        </button>
        <button type="button" className="danger" onClick={() => blocker.proceed()}>
          Discard and leave
        </button>
      </div>
    </dialog>
  )
}

export function ExecutionBoundary() {
  return (
    <div className="notice preparation-boundary">
      <div>
        <strong>Preparation only. Execution disabled.</strong>
        <p>
          Preparation approval is not authorization to execute. No target is contacted, and there is
          no execution evidence.
        </p>
      </div>
      <button type="button" disabled aria-describedby="execution-boundary">
        Execution disabled
      </button>
      <span id="execution-boundary" className="sr-only">
        This release cannot run exercises or write to connected systems.
      </span>
    </div>
  )
}
