import { LabError, type PendingMutation } from './api';

function key(accountId: string) { return `flood-lab:pending:v1:${accountId}`; }

export function readPending(accountId: string): PendingMutation | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key(accountId)) ?? 'null');
    if (!value || typeof value !== 'object') return null;
    const pending = value as Partial<PendingMutation>;
    if (
      typeof pending.runId !== 'string' ||
      typeof pending.requestId !== 'string' ||
      !['acknowledge', 'allocate'].includes(pending.operation ?? '') ||
      typeof pending.body?.idempotency_key !== 'string' ||
      typeof pending.body?.expected_version !== 'string'
    ) return null;
    return pending as PendingMutation;
  } catch { return null; }
}

export function writePending(accountId: string, pending: PendingMutation | null): void {
  try {
    if (pending) sessionStorage.setItem(key(accountId), JSON.stringify(pending));
    else sessionStorage.removeItem(key(accountId));
  } catch {
    if (pending) throw new LabError(
      'failed',
      'reconciliation_storage_required',
      'Enable session storage before changing a request. No operation was sent.',
    );
    // A leftover confirmed journal entry is safe: replay returns the original receipt.
  }
}
