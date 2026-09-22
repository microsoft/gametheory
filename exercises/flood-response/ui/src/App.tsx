import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import {
  LabError,
  type LabEvent,
  type Operations,
  type PendingMutation,
  type ResourceRequest,
  type Run,
} from './api';
import type { Authentication, Identity } from './auth';
import { readPending, writePending } from './journal';

function utc(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unavailable' : `${date.toISOString().slice(0, 19).replace('T', ' ')} UTC`;
}

function errorOf(value: unknown): LabError {
  return value instanceof LabError
    ? value
    : new LabError('failed', 'action_unavailable', 'This action is unavailable. Sign in again or contact the operator.');
}

function ErrorNotice({ error }: { error: LabError }) {
  return (
    <div className="notice error" role="alert">
      <strong>{error.outcome === 'unknown' ? 'Result unknown' : error.code === 'version_conflict' ? 'This request changed' : 'Action not completed'}</strong>
      <p>{error.message}</p>
      {error.correlationId && <p className="metadata">Correlation <code>{error.correlationId}</code></p>}
    </div>
  );
}

export function App({ auth, operations }: { auth: Authentication | null; operations: Operations | null }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [authReady, setAuthReady] = useState(!auth);
  const [authBusy, setAuthBusy] = useState(false);
  const [error, setError] = useState<LabError | null>(null);

  useEffect(() => {
    let active = true;
    auth?.initialize()
      .then((user) => { if (active) setIdentity(user); })
      .catch((reason) => { if (active) setError(errorOf(reason)); })
      .finally(() => { if (active) setAuthReady(true); });
    return () => { active = false; };
  }, [auth]);

  async function signIn() {
    if (!auth) return;
    setAuthBusy(true);
    setError(null);
    try { setIdentity(await auth.signIn()); }
    catch (reason) { setError(errorOf(reason)); }
    finally { setAuthBusy(false); }
  }

  async function signOut() {
    if (!auth) return;
    setAuthBusy(true);
    try { await auth.signOut(); setIdentity(null); }
    catch (reason) { setError(errorOf(reason)); }
    finally { setAuthBusy(false); }
  }

  return (
    <>
      <a className="skip-link" href="#main">Skip to operations</a>
      <div className="exercise-banner">EXERCISE ONLY · Fictional people, places and requests. Not for emergency use.</div>
      <header className="app-header">
        <div><h1>Flood response operations</h1><p>Independent shelter-capacity exercise</p></div>
        {identity && <div className="account"><span>{identity.displayName}</span><button onClick={signOut} disabled={authBusy}>Sign out</button></div>}
      </header>
      <main id="main" tabIndex={-1}>
        {error && <ErrorNotice error={error} />}
        {!auth || !operations ? (
          <section className="setup" aria-labelledby="setup-title">
            <h2 id="setup-title">Setup required</h2>
            <p>Configure the lab’s Entra tenant, SPA application, API scope and independent API address, then rebuild this UI.</p>
            <p>No test sign-in or sample session is enabled in this deployment. Ask the exercise operator to follow the lab README.</p>
          </section>
        ) : !authReady ? (
          <div className="loading" role="status">Checking your lab sign-in…</div>
        ) : !identity ? (
          <section className="setup">
            <h2>Sign in to your exercise</h2>
            <p>Your account needs an explicit grant for this run. Signing in does not grant operator or exercise-approval powers.</p>
            <button className="primary" onClick={signIn} disabled={authBusy}>{authBusy ? 'Signing in…' : 'Sign in with Microsoft'}</button>
          </section>
        ) : <Workspace key={identity.accountId} accountId={identity.accountId} operations={operations} />}
      </main>
      <footer>Notification sending is disabled. No message has been submitted to Microsoft Graph by this application.</footer>
    </>
  );
}

function Workspace({ operations, accountId }: { operations: Operations; accountId: string }) {
  const [pending, setPending] = useState<PendingMutation | null>(() => readPending(accountId));
  const [reconciling, setReconciling] = useState(false);
  const [writeBusy, setWriteBusy] = useState(false);
  const [reconciled, setReconciled] = useState('');
  const [runs, setRuns] = useState<Run[]>([]);
  const [nextRuns, setNextRuns] = useState<number | null>(null);
  const [runId, setRunId] = useState('');
  const [requests, setRequests] = useState<ResourceRequest[]>([]);
  const [nextRequests, setNextRequests] = useState<number | null>(null);
  const [selected, setSelected] = useState<ResourceRequest | null>(null);
  const [loading, setLoading] = useState(true);
  const [runLoading, setRunLoading] = useState(false);
  const [error, setError] = useState<LabError | null>(null);
  const generation = useRef(0);
  const currentRun = runs.find((run) => run.run_id === runId);

  useEffect(() => {
    let active = true;
    operations.runs().then((page) => {
      if (!active) return;
      setRuns(page.items);
      setNextRuns(page.next_offset);
      setRunId(pending?.runId ?? page.items[0]?.run_id ?? '');
    }).catch((reason) => { if (active) setError(errorOf(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [operations]);

  const loadRequests = useCallback(async (id: string, after = 0) => {
    const stamp = ++generation.current;
    setRunLoading(true);
    setError(null);
    try {
      const page = await operations.requests(id, after);
      if (stamp !== generation.current) return;
      setRequests((previous) => after ? [...previous, ...page.items] : page.items);
      setNextRequests(page.next_after);
    } catch (reason) {
      if (stamp === generation.current) setError(errorOf(reason));
    } finally {
      if (stamp === generation.current) setRunLoading(false);
    }
  }, [operations]);

  useEffect(() => {
    setSelected(null);
    setRequests([]);
    if (runId) void loadRequests(runId);
    return () => { generation.current += 1; };
  }, [runId, loadRequests]);

  async function moreRuns() {
    if (nextRuns === null) return;
    try {
      const page = await operations.runs(nextRuns);
      setRuns((previous) => [...previous, ...page.items]);
      setNextRuns(page.next_offset);
    } catch (reason) { setError(errorOf(reason)); }
  }

  function update(record: ResourceRequest) {
    setSelected(record);
    setRequests((previous) => previous.map((entry) => entry.request_id === record.request_id ? record : entry));
  }

  function remember(mutation: PendingMutation | null) {
    writePending(accountId, mutation);
    setPending(mutation);
  }

  async function reconcile() {
    if (!pending) return;
    setReconciling(true);
    setError(null);
    try {
      const response = await operations.mutate(pending);
      remember(null);
      setRunId(response.run_id);
      update(response);
      setReconciled(`Original operation confirmed. Durable event ${response.durable_event_id}. Read the latest record before making another change.`);
    } catch (reason) {
      const failure = errorOf(reason);
      if (failure.outcome === 'rejected' && failure.durableEventId) {
        setError(failure);
        remember(null);
      } else {
        setError(new LabError(
          'unknown', 'reconciliation_blocked',
          `The original result is still unknown. ${failure.message} Keep the original key; ask the operator to inspect its receipt if access cannot be restored.`,
          failure.correlationId,
        ));
      }
    } finally { setReconciling(false); }
  }

  if (loading) return <div className="loading" role="status">Loading authorized exercise runs…</div>;
  return (
    <>
      {error && <ErrorNotice error={error} />}
      {reconciled && <p className="notice" role="status">{reconciled}</p>}
      {pending && <div className="reconcile">
        <strong>Keep the original operation until its result is confirmed</strong>
        <p>The account-scoped session journal retains the exact inputs and key across reloads. Do not start another operation.</p>
        <code>{pending.body.idempotency_key}</code>
        <button onClick={reconcile} disabled={reconciling || writeBusy} className="primary">{reconciling ? 'Reconciling…' : 'Reconcile original operation'}</button>
      </div>}
      {!runs.length ? (
        <section className="empty"><h2>No authorized runs</h2><p>Ask the exercise operator for a time-limited run grant for your signed-in account. Run IDs do not grant access.</p></section>
      ) : (
        <>
          <div className="workspace-toolbar">
            <label>Exercise run<select value={runId} disabled={!!pending} onChange={(event) => setRunId(event.target.value)}>{runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.name}</option>)}</select></label>
            <p className="role">Access: <strong>{currentRun?.role}</strong> · Run: {currentRun?.status}</p>
            {nextRuns !== null && <button onClick={moreRuns}>Load more runs</button>}
          </div>
          <div className="workspace">
            <section className="request-pane" aria-labelledby="requests-title" aria-busy={runLoading}>
              <div className="section-heading"><h2 id="requests-title">Resource requests</h2><button onClick={() => loadRequests(runId)} disabled={runLoading}>Refresh list</button></div>
              {runLoading && <div className="loading" role="status">Loading requests…</div>}
              {!runLoading && !requests.length && <div className="empty"><h3>No requests in this run</h3><p>Requests appear here after an authorized exercise service creates them. Participants cannot seed or reset runs.</p></div>}
              <ul className="request-list">
                {requests.map((record) => (
                  <li key={record.request_id}>
                    <button className="request-choice" disabled={!!pending} aria-pressed={selected?.request_id === record.request_id} onClick={() => setSelected(record)}>
                      <span className="request-title">{record.summary}</span>
                      <span className={`status ${record.status}`}>{record.status}</span>
                      <span className="request-summary">{record.quantity_allocated} / {record.quantity_requested} {record.resource_type.replaceAll('_', ' ')} allocated</span>
                      <span className="metadata">Created {utc(record.created_at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
              {nextRequests !== null && <button onClick={() => loadRequests(runId, nextRequests)} disabled={runLoading}>Load more requests</button>}
            </section>
            {selected ? (
              <RequestDetail key={`${runId}:${selected.request_id}`} record={selected} operations={operations} canChange={currentRun?.role === 'participant' && currentRun.status === 'active'} onUpdate={update} pending={pending} onPendingChange={remember} onMutationBusy={setWriteBusy} />
            ) : <section className="detail-pane empty"><h2>Select a request</h2><p>Read the requested quantities and timing, then acknowledge ownership or record an allocation.</p><p>Demonstration goals: acknowledge within 10 minutes and allocate the full quantity within 20 minutes of committed creation.</p></section>}
          </div>
        </>
      )}
    </>
  );
}

function RequestDetail({ record, operations, canChange, onUpdate, pending, onPendingChange, onMutationBusy }: {
  record: ResourceRequest; operations: Operations; canChange: boolean; onUpdate: (record: ResourceRequest) => void;
  pending: PendingMutation | null; onPendingChange: (pending: PendingMutation | null) => void;
  onMutationBusy: (busy: boolean) => void;
}) {
  const [quantity, setQuantity] = useState(String(Math.max(1, record.quantity_requested - record.quantity_allocated)));
  const [available, setAvailable] = useState(new Date().toISOString().slice(0, 16));
  const [error, setError] = useState<LabError | null>(null);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [events, setEvents] = useState<LabEvent[]>([]);
  const [evidenceTruncated, setEvidenceTruncated] = useState(false);
  const [evidenceState, setEvidenceState] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle');
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    function beforeUnload(event: BeforeUnloadEvent) {
      if (pending) { event.preventDefault(); }
    }
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [pending]);

  async function submit(mutation: PendingMutation) {
    setBusy(true);
    onMutationBusy(true);
    setError(null);
    setNotice('');
    try {
      onPendingChange(mutation);
      const result = await operations.mutate(mutation);
      if (!alive.current) return;
      onUpdate(result);
      onPendingChange(null);
      setNotice(`Confirmed ${mutation.operation === 'allocate' ? 'allocation' : 'acknowledgement'} at ${utc(result.committed_at)}. Event ${result.durable_event_id}.`);
      setEvidenceState('idle');
    } catch (reason) {
      if (!alive.current) return;
      const failure = errorOf(reason);
      setError(failure);
      // Failed means not sent; rejected means a known non-application. Unknown keeps the exact key.
      if (failure.outcome !== 'unknown') onPendingChange(null);
    } finally {
      onMutationBusy(false);
      if (alive.current) setBusy(false);
    }
  }

  function act(operation: 'acknowledge' | 'allocate', event?: FormEvent) {
    event?.preventDefault();
    const amount = Number(quantity);
    if (operation === 'allocate' && (!Number.isInteger(amount) || amount < 1 || amount > record.quantity_requested - record.quantity_allocated || !available)) {
      setError(new LabError('rejected', 'invalid_input', 'Enter a whole quantity within the remaining request and an availability time in UTC.'));
      return;
    }
    void submit({
      runId: record.run_id,
      requestId: record.request_id,
      operation,
      body: {
        idempotency_key: crypto.randomUUID(),
        expected_version: record.record_version,
        ...(operation === 'allocate' ? { quantity: amount, available_at: `${available}:00Z` } : {}),
      },
    });
  }

  async function refreshRecord() {
    setBusy(true);
    try {
      const fresh = await operations.request(record.run_id, record.request_id);
      if (!alive.current) return;
      onUpdate(fresh);
      setError(null);
      setNotice('Latest record loaded. Your allocation inputs are preserved; review them before submitting.');
    } catch (reason) { if (alive.current) setError(errorOf(reason)); }
    finally { if (alive.current) setBusy(false); }
  }

  async function loadEvidence() {
    setEvidenceState('loading');
    try {
      const page = await operations.events(record.run_id, record.request_id);
      if (!alive.current) return;
      setEvents(page.items);
      setEvidenceTruncated(page.next_after !== null);
      setEvidenceState('ready');
    } catch {
      if (alive.current) setEvidenceState('failed');
    }
  }

  const locked = busy || !!pending;
  return (
    <section className="detail-pane" aria-labelledby="detail-title">
      <div className="section-heading"><h2 id="detail-title">Request detail</h2><span className={`status ${record.status}`}>{record.status}</span></div>
      <p className="detail-summary">{record.summary}</p>
      <dl className="facts">
        <div><dt>Resource</dt><dd>{record.resource_type.replaceAll('_', ' ')}</dd></div>
        <div><dt>Allocated / requested</dt><dd>{record.quantity_allocated} / {record.quantity_requested}</dd></div>
        <div><dt>Committed creation</dt><dd>{utc(record.created_at)}</dd></div>
        <div><dt>Needed by</dt><dd>{utc(record.needed_by)}</dd></div>
        <div><dt>Acknowledged</dt><dd>{record.acknowledged_at ? utc(record.acknowledged_at) : 'Not yet acknowledged'}</dd></div>
      </dl>
      <details className="identifiers"><summary>Record identifiers and version</summary><p>Request <code>{record.request_id}</code></p><p>Shelter <code>{record.shelter_id}</code></p><p>Version <code>{record.record_version}</code></p></details>
      {error && <ErrorNotice error={error} />}
      {notice && <div className="notice" role="status">{notice}</div>}
      {!canChange ? <p className="read-only">Read-only access. A participant grant and active run are required to make changes.</p> : (
        <div className="actions">
          {record.status === 'open' && <><h3>Acknowledge ownership</h3><p>Confirm you have seen this request and will coordinate its response.</p><button className="primary" onClick={() => act('acknowledge')} disabled={locked}>{busy ? 'Confirming…' : 'Acknowledge request'}</button></>}
          {record.status === 'acknowledged' && <form onSubmit={(event) => act('allocate', event)}>
            <h3>Allocate resources</h3>
            <p>{record.quantity_requested - record.quantity_allocated} {record.resource_type.replaceAll('_', ' ')} remain. Partial allocations are recorded; the goal requires the full quantity.</p>
            <div className="allocation-fields">
              <label>Quantity<input type="number" min="1" max={record.quantity_requested - record.quantity_allocated} step="1" required value={quantity} onChange={(event) => setQuantity(event.target.value)} disabled={locked} /></label>
              <label>Available at (UTC)<input type="datetime-local" required value={available} onChange={(event) => setAvailable(event.target.value)} disabled={locked} /></label>
            </div>
            <button className="primary" type="submit" disabled={locked}>{busy ? 'Recording…' : 'Record allocation'}</button>
          </form>}
          {record.status === 'fulfilled' && <p className="fulfilled-message">The requested quantity is fully allocated. Review committed evidence below.</p>}
        </div>
      )}
      <button className="refresh-record" onClick={refreshRecord} disabled={locked}>Read latest record</button>
      <section className="evidence" aria-labelledby="evidence-title">
        <div className="section-heading"><h3 id="evidence-title">Durable request evidence</h3><button onClick={loadEvidence} disabled={evidenceState === 'loading'}>Read events</button></div>
        {evidenceState === 'idle' && <p>Events are read from this run’s independent database. No email-delivery evidence is available.</p>}
        {evidenceState === 'loading' && <p role="status">Reading committed events…</p>}
        {evidenceState === 'failed' && <p role="alert">Evidence could not be read. Assessment is indeterminate; no participant failure is inferred.</p>}
        {evidenceState === 'ready' && !events.length && <p>No matching evidence was returned. Assessment is indeterminate.</p>}
        {evidenceTruncated && <p>Showing the first 100 events. This is incomplete evidence, not a final assessment.</p>}
        <ol className="event-list">{events.map((event) => <li key={event.durable_event_id}><strong>{event.operation}</strong> · {event.outcome}<span>{utc(event.committed_at)}</span><code>{event.durable_event_id}</code></li>)}</ol>
      </section>
    </section>
  );
}
