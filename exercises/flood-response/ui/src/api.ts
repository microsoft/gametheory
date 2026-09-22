import type { Authentication } from './auth';

export type Outcome = 'succeeded' | 'rejected' | 'failed' | 'unknown';
export type Run = {
  run_id: string;
  name: string;
  status: string;
  role: 'participant' | 'api' | 'observer';
};
export type ResourceRequest = {
  request_id: string;
  sequence: number;
  run_id: string;
  shelter_id: string;
  resource_type: 'cots' | 'blankets' | 'water_cases' | 'transport_seats';
  quantity_requested: number;
  quantity_allocated: number;
  status: 'open' | 'acknowledged' | 'fulfilled';
  summary: string;
  needed_by: string;
  created_at: string;
  acknowledged_at: string | null;
  record_version: string;
};
export type LabEvent = {
  durable_event_id: string;
  sequence: number;
  run_id: string;
  operation: string;
  outcome: Outcome;
  record_id: string | null;
  record_version: string | null;
  committed_at: string;
  correlation_id: string;
  data: Record<string, string | number | boolean | null>;
};
export type MutationResult = ResourceRequest & {
  outcome: 'succeeded';
  durable_event_id: string;
  committed_at: string;
  correlation_id: string;
  allocation_id: string | null;
};
export type PendingMutation = {
  runId: string;
  requestId: string;
  operation: 'acknowledge' | 'allocate';
  // Journaled inputs, not wire JSON: reserved controls are extracted into fixed headers.
  body: {
    idempotency_key: string;
    expected_version: string;
    quantity?: number;
    available_at?: string;
  };
};
export type Page<T> = { items: T[]; next_after: number | null };

export interface Operations {
  runs(offset?: number): Promise<{ items: Run[]; next_offset: number | null }>;
  requests(runId: string, after?: number): Promise<Page<ResourceRequest>>;
  request(runId: string, requestId: string): Promise<ResourceRequest>;
  events(runId: string, requestId: string): Promise<Page<LabEvent>>;
  mutate(pending: PendingMutation): Promise<MutationResult>;
}

export class LabError extends Error {
  constructor(
    readonly outcome: Exclude<Outcome, 'succeeded'>,
    readonly code: string,
    message: string,
    readonly correlationId: string | null = null,
    readonly durableEventId: string | null = null,
  ) {
    super(message);
  }
}

export class LabClient implements Operations {
  constructor(private readonly base: string, private readonly auth: Authentication) {}

  private async call<T>(path: string, mutation?: PendingMutation): Promise<T> {
    let accessToken: string;
    try {
      accessToken = await this.auth.token();
    } catch {
      throw new LabError('failed', 'sign_in_required', 'Sign out and sign in again to continue.');
    }
    let response: Response;
    try {
      response = await fetch(`${this.base}${path}`, {
        method: mutation ? 'POST' : 'GET',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          ...(mutation
            ? {
              'Content-Type': 'application/json',
              'If-Match': `"${mutation.body.expected_version}"`,
              'Idempotency-Key': mutation.body.idempotency_key,
            }
            : {}),
        },
        body: mutation ? JSON.stringify({
          ...(mutation.body.quantity === undefined ? {} : { quantity: mutation.body.quantity }),
          ...(mutation.body.available_at === undefined ? {} : { available_at: mutation.body.available_at }),
        }) : undefined,
        cache: 'no-store',
        credentials: 'omit',
        redirect: 'error',
        signal: AbortSignal.timeout(15000),
      });
    } catch {
      throw new LabError(
        mutation ? 'unknown' : 'failed',
        'network_unavailable',
        mutation
          ? 'The result is unknown. Keep these inputs and reconcile the same operation.'
          : 'The lab could not be reached. Check the connection and refresh.',
      );
    }
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new LabError(
        mutation ? 'unknown' : 'failed',
        'invalid_response',
        'No readable result was returned. Keep the operation key and contact the operator.',
        response.headers.get('X-Correlation-ID'),
      );
    }
    if (!response.ok) {
      const error = body && typeof body === 'object' ? body as Record<string, unknown> : {};
      const outcome = ['rejected', 'failed', 'unknown'].includes(String(error.outcome))
        ? (error.outcome as Exclude<Outcome, 'succeeded'>)
        : mutation ? 'unknown' : 'failed';
      throw new LabError(
        outcome,
        typeof error.code === 'string' ? error.code : 'request_failed',
        typeof error.message === 'string' ? error.message : 'The operation was not confirmed.',
        typeof error.correlation_id === 'string'
          ? error.correlation_id
          : response.headers.get('X-Correlation-ID'),
        typeof error.durable_event_id === 'string' ? error.durable_event_id : null,
      );
    }
    if (mutation) {
      const record = body as Partial<MutationResult> | null;
      const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
      if (
        !record ||
        record.outcome !== 'succeeded' ||
        record.run_id !== mutation.runId ||
        record.request_id !== mutation.requestId ||
        !uuid.test(record.durable_event_id ?? '') ||
        !uuid.test(record.correlation_id ?? '') ||
        !/^v1:[0-9a-f]{32}$/.test(record.record_version ?? '') ||
        !/(?:Z|\+00:00)$/.test(record.committed_at ?? '') ||
        !Number.isFinite(Date.parse(record.committed_at ?? ''))
      ) throw new LabError(
        'unknown', 'invalid_receipt', 'The server did not return a valid durable receipt. Reconcile the original operation.',
        response.headers.get('X-Correlation-ID'),
      );
    }
    return body as T;
  }

  runs(offset = 0) {
    return this.call<{ items: Run[]; next_offset: number | null }>(`/v1/runs?limit=50&offset=${offset}`);
  }
  requests(runId: string, after = 0) {
    return this.call<Page<ResourceRequest>>(
      `/v1/runs/${encodeURIComponent(runId)}/requests?limit=50&after=${after}`,
    );
  }
  request(runId: string, requestId: string) {
    return this.call<ResourceRequest>(
      `/v1/runs/${encodeURIComponent(runId)}/requests/${encodeURIComponent(requestId)}`,
    );
  }
  events(runId: string, requestId: string) {
    return this.call<Page<LabEvent>>(
      `/v1/runs/${encodeURIComponent(runId)}/events?limit=100&record_id=${encodeURIComponent(requestId)}`,
    );
  }
  mutate(pending: PendingMutation) {
    return this.call<MutationResult>(
      `/v1/runs/${encodeURIComponent(pending.runId)}/requests/${encodeURIComponent(pending.requestId)}/${pending.operation}`,
      pending,
    );
  }
}
