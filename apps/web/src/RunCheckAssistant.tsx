import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowUp, CheckCircle2, FileText, Sparkles } from 'lucide-react'
import type { components } from './api.generated'
import { ApiError, ErrorNotice, useSession } from './api'
import type { Preview } from './preparation'
import type { RunSetupDraft, SetupModel } from './runSetup'
import {
  activeSuggestion,
  applySuggestion,
  currentFor,
  describeSuggestion,
  entryCount,
  EVERY_GOAL_PROMPT,
  hasChecks,
  usableCount,
  type SuggestedCheck,
  type SuggestedEntries,
  type Suggestion,
} from './runChecks'

type Accepted = components['schemas']['RunCheckRequestAccepted']
type Mode = 'replace' | 'merge'
const ADDED_NOTE =
  'Added to the forms below. Review each one, then check the setup before creating.'
const NOTHING_NEW_NOTE = 'Nothing new to add. Your forms are unchanged.'

const statusLabel: Record<Suggestion['status'], string> = {
  queued: 'Waiting to start',
  running: 'Suggesting checks…',
  proposed: 'Suggestions ready',
  failed: 'No suggestions',
}
const sections: ReadonlyArray<readonly [SuggestedCheck['section'], string]> = [
  ['watch', 'Watch for a condition'],
  ['goal', 'How goals are judged'],
  ['undo', 'Undo plan'],
]

function unavailable(error: unknown) {
  return error instanceof ApiError && error.status === 503
}

export function RunCheckAssistant({
  wid,
  bid,
  preview,
  model,
  draft,
  ready,
  disabled,
  prompt,
  onPromptChange,
  onApply,
}: {
  wid: string
  bid: string
  preview: Preview
  model: SetupModel
  draft: RunSetupDraft
  ready: boolean
  disabled: boolean
  prompt: string
  onPromptChange: (prompt: string) => void
  /** Called only when a suggestion filled at least one entry, which `added` lists. */
  onApply: (draft: RunSetupDraft, item: Suggestion, added: SuggestedEntries) => void
}) {
  const { api } = useSession()
  const cache = useQueryClient()
  const path = `/workspaces/${wid}/boards/${bid}/run-setup/suggestions`
  const key = [wid, 'run-check-suggestions', bid]
  const intent = useRef<{ id: string; prompt: string; preview: string }>()
  const [applied, setApplied] = useState<{ id: string; notes: string[] }>()
  const history = useQuery({
    queryKey: key,
    queryFn: () => api.get<Suggestion[]>(path),
    refetchInterval: (query) => (query.state.data?.some(activeSuggestion) ? 3000 : false),
  })
  const ask = useMutation({
    mutationFn: (text: string) => {
      if (!ready)
        throw new Error('Save the preparation and freeze a current preview before asking.')
      if (
        !intent.current ||
        intent.current.prompt !== text ||
        intent.current.preview !== preview.id
      )
        intent.current = { id: crypto.randomUUID(), prompt: text, preview: preview.id }
      return api.send<Accepted>(path, 'POST', {
        preview_id: preview.id,
        preview_digest: preview.digest,
        prompt: text,
        request_id: intent.current.id,
      })
    },
    onSuccess: (_, text) => {
      intent.current = undefined
      if (text === prompt) onPromptChange('')
      void cache.invalidateQueries({ queryKey: key })
    },
    // Another operator's request may be active, or the preview may have moved on.
    onError: () => void cache.invalidateQueries({ queryKey: key }),
  })
  const items = history.data ?? []
  const working = items.some(activeSuggestion)
  const blocked = !ready || working || ask.isPending || disabled
  const reason = !ready
    ? 'Save the preparation and freeze a current preview first. Suggestions are checked against that exact preview.'
    : working
      ? 'The assistant is working on the latest request. You can keep editing the forms meanwhile.'
      : 'Suggestions reach the forms only when you choose. They never add notifications, recipients, or approvals, and never create or start a run.'

  function submit(text: string) {
    if (text.trim() && !blocked) ask.mutate(text)
  }
  function use(item: Suggestion, mode: Mode) {
    const result = applySuggestion(item, model, draft, mode)
    // Filling nothing changes nothing: the forms and any recorded suggestion stay as they are.
    if (!entryCount(result.added)) {
      setApplied({ id: item.id, notes: [NOTHING_NEW_NOTE, ...result.notes] })
      return
    }
    if (
      mode === 'replace' &&
      hasChecks(draft) &&
      !window.confirm(
        'Replace your current checks, goal rules, and undo plan with these suggestions? Your start choice is kept.',
      )
    )
      return
    onApply(result.draft, item, result.added)
    setApplied({ id: item.id, notes: result.notes.length ? result.notes : [ADDED_NOTE] })
  }

  const turn = (item: Suggestion, live = false) => (
    <SuggestionTurn
      key={item.id}
      item={item}
      live={live}
      model={model}
      current={currentFor(item, preview.id)}
      busy={disabled || ask.isPending}
      canAsk={!blocked}
      canReuse={!prompt.trim()}
      notes={applied?.id === item.id ? applied.notes : undefined}
      onUse={(mode) => use(item, mode)}
      onAskAgain={() => submit(item.prompt)}
      onReuse={() => onPromptChange(item.prompt)}
    />
  )
  const [latest, ...earlier] = items
  return (
    <section
      className="glass preparation-panel run-check-assistant"
      aria-labelledby="run-check-assistant-heading"
    >
      <div className="run-check-heading">
        <span className="assistant-icon" aria-hidden="true">
          <Sparkles size={18} />
        </span>
        <div>
          <h2 id="run-check-assistant-heading">Describe what to check</h2>
          <p>
            Say in your own words what this run should watch and how each goal is judged. The
            assistant suggests checks you can review before anything reaches the forms below.
          </p>
        </div>
      </div>
      {unavailable(history.error) || unavailable(ask.error) ? (
        <p className="notice" role="status">
          The run-check assistant is not configured for this deployment. The setup forms below work
          the same without it.
        </p>
      ) : (
        <>
          <form
            className="run-check-composer"
            onSubmit={(event) => {
              event.preventDefault()
              submit(prompt)
            }}
          >
            <label htmlFor="run-check-prompt">What should this run check?</label>
            <textarea
              id="run-check-prompt"
              value={prompt}
              maxLength={4000}
              disabled={disabled}
              aria-describedby="run-check-reason"
              placeholder="For example: make sure they acknowledge within 10 minutes, and watch occupancy until it’s above 85%."
              onChange={(event) => onPromptChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault()
                  submit(prompt)
                }
              }}
            />
            <div className="run-check-actions">
              <button type="submit" className="primary" disabled={blocked || !prompt.trim()}>
                <ArrowUp size={16} aria-hidden="true" />
                {ask.isPending && ask.variables === prompt ? 'Asking…' : 'Suggest checks'}
              </button>
              <button type="button" disabled={blocked} onClick={() => submit(EVERY_GOAL_PROMPT)}>
                {ask.isPending && ask.variables === EVERY_GOAL_PROMPT
                  ? 'Asking…'
                  : 'Suggest checks for every goal'}
              </button>
              <span className="run-check-context">
                <FileText size={14} aria-hidden="true" />
                Preview {preview.sequence} · board version {preview.board_version}
              </span>
            </div>
            <p id="run-check-reason" className="preparation-note">
              {reason}
            </p>
            <ErrorNotice error={ask.error} />
          </form>
          <ErrorNotice error={history.error} />
          {history.isPending && <p role="status">Loading suggestion requests…</p>}
          {!history.isPending && !history.error && !items.length && (
            <p className="preparation-note">
              No suggestions yet. Describe what matters in your own words, or ask for checks for
              every goal.
            </p>
          )}
          {latest && (
            <>
              <h3 className="run-check-history-heading">Latest request</h3>
              <ol className="run-check-history">{turn(latest, true)}</ol>
            </>
          )}
          {earlier.length > 0 && (
            <details className="run-check-earlier">
              <summary>Earlier requests ({earlier.length})</summary>
              <ol className="run-check-history">{earlier.map((item) => turn(item))}</ol>
            </details>
          )}
        </>
      )}
    </section>
  )
}

function SuggestionTurn({
  item,
  live,
  model,
  current,
  busy,
  canAsk,
  canReuse,
  notes,
  onUse,
  onAskAgain,
  onReuse,
}: {
  item: Suggestion
  live: boolean
  model: SetupModel
  current: boolean
  busy: boolean
  canAsk: boolean
  canReuse: boolean
  notes?: string[]
  onUse: (mode: Mode) => void
  onAskAgain: () => void
  onReuse: () => void
}) {
  const proposed = item.status === 'proposed' && !item.error
  const checks = proposed && current ? describeSuggestion(model, item) : []
  const usable = proposed && current ? usableCount(item) : 0
  const total = item.observations.length + item.objectives.length + item.recovery.length
  return (
    <li className="run-check-turn">
      <div className="run-check-request">
        <p>{item.prompt}</p>
        <small>
          <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time>
        </small>
      </div>
      <div className="run-check-reply">
        <div className="run-check-status" role={live ? 'status' : undefined}>
          <span className={`pill${item.status === 'failed' ? ' danger' : ''}`}>
            {statusLabel[item.status]}
          </span>
          {!current && <span className="pill">Earlier preview</span>}
        </div>
        {item.error && <p className="error-text">{item.error}</p>}
        {item.summary && <p>{item.summary}</p>}
        {item.questions.length > 0 && (
          <div className="notice run-check-questions">
            <strong>Questions for you</strong>
            <ul>
              {item.questions.map((question, index) => (
                <li key={index}>{question}</li>
              ))}
            </ul>
          </div>
        )}
        {proposed && !current && (
          <p className="preparation-note">
            {total
              ? `${total === 1 ? 'This suggestion was' : `These ${total} suggestions were`} reviewed against an earlier preview, so ${total === 1 ? 'it can’t' : 'they can’t'} be used here.`
              : 'This answer was for an earlier preview.'}{' '}
            <button type="button" className="link-button" disabled={!canAsk} onClick={onAskAgain}>
              Ask again for the current preview
            </button>
          </p>
        )}
        {sections.map(([section, heading]) => {
          const group = checks.filter((check) => check.section === section)
          return group.length ? (
            <div key={section}>
              <h4>{heading}</h4>
              <ul className="run-check-items">
                {group.map((check) => (
                  <SuggestedItem key={check.key} check={check} />
                ))}
              </ul>
            </div>
          ) : null
        })}
        {proposed && current && total > 0 && (
          <div className="run-check-actions">
            <button
              type="button"
              className="primary"
              disabled={busy || !usable}
              onClick={() => onUse('replace')}
            >
              Use these suggestions
            </button>
            <button type="button" disabled={busy || !usable} onClick={() => onUse('merge')}>
              Add to my checks
            </button>
            <span className="preparation-note">
              {usable
                ? `Uses ${usable === total ? `all ${total}` : `the ${usable} valid`} of ${total} suggested ${total === 1 ? 'item' : 'items'}. You still review, check, and create the run.`
                : 'None of these can be used as they are. Rephrase the request or set the checks in the forms.'}
            </span>
          </div>
        )}
        {item.status === 'failed' && (
          <div className="run-check-actions">
            <button type="button" disabled={!canReuse} onClick={onReuse}>
              Use this request again
            </button>
          </div>
        )}
        {notes && (
          <ul className="run-check-notes" role="status">
            {notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        )}
      </div>
    </li>
  )
}

function SuggestedItem({ check }: { check: SuggestedCheck }) {
  return (
    <li className="run-check-item">
      <span className={`run-check-badge ${check.valid ? 'ready' : 'attention'}`}>
        {check.valid ? (
          <CheckCircle2 size={14} aria-hidden="true" />
        ) : (
          <AlertTriangle size={14} aria-hidden="true" />
        )}
        {check.valid ? 'Valid' : 'Needs changes'}
      </span>
      <div>
        {check.title && <strong>{check.title}</strong>}
        <p>{check.text}</p>
        {check.issues.length > 0 && (
          <ul className="setup-problems">
            {check.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        )}
      </div>
    </li>
  )
}
