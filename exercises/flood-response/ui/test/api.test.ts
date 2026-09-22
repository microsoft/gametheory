import { afterEach, describe, expect, it, vi } from 'vitest';
import { LabClient, LabError, type PendingMutation } from '../src/api';
import { configuration } from '../src/config';
import { readPending, writePending } from '../src/journal';
import { testAuth, testRecord, TEST_REQUEST_ID, TEST_RUN_ID } from './fixtures';

afterEach(() => vi.unstubAllGlobals());

const pending: PendingMutation = {
  runId: TEST_RUN_ID,
  requestId: TEST_REQUEST_ID,
  operation: 'acknowledge',
  body: { idempotency_key: 'TEST-ONLY', expected_version: testRecord.record_version },
};

describe('real transport boundary', () => {
  it('sends the actual access token, fixed precondition and exact mutation inputs', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...testRecord,
      outcome: 'succeeded',
      durable_event_id: '77777777-7777-5777-8777-777777777777',
      committed_at: '2030-01-01T10:05:00Z',
      correlation_id: '88888888-8888-5888-8888-888888888888',
    }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetch);
    await new LabClient('http://localhost:8088', testAuth).mutate(pending);
    const init = fetch.mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer TEST-ONLY-NOT-A-REAL-TOKEN',
      'If-Match': `"${pending.body.expected_version}"`,
      'Idempotency-Key': pending.body.idempotency_key,
    });
    expect(init.redirect).toBe('error');
    expect(init.credentials).toBe('omit');
    expect(JSON.parse(init.body as string)).toEqual({});
  });

  it('distinguishes rejected and unknown without manufacturing success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('TEST ONLY transport timeout')));
    await expect(new LabClient('http://localhost:8088', testAuth).mutate(pending))
      .rejects.toMatchObject({ outcome: 'unknown' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      outcome: 'rejected', code: 'version_conflict', message: 'Read again.', correlation_id: 'TEST ONLY',
    }), { status: 409 })));
    await expect(new LabClient('http://localhost:8088', testAuth).mutate(pending))
      .rejects.toMatchObject({ outcome: 'rejected', code: 'version_conflict' });
  });

  it('sends only allocation domain fields as JSON, never reserved controls', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...testRecord,
      outcome: 'succeeded',
      durable_event_id: '77777777-7777-5777-8777-777777777777',
      committed_at: '2030-01-01T10:05:00Z',
      correlation_id: '88888888-8888-5888-8888-888888888888',
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    await new LabClient('http://localhost:8088', testAuth).mutate({
      ...pending,
      operation: 'allocate',
      body: { ...pending.body, quantity: 4, available_at: '2030-01-01T10:05:00Z' },
    });
    const init = fetch.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      quantity: 4, available_at: '2030-01-01T10:05:00Z',
    });
    expect(init.headers).toMatchObject({
      'If-Match': `"${testRecord.record_version}"`,
      'Idempotency-Key': 'TEST-ONLY',
    });
  });

  it('requires explicit HTTPS or loopback API and valid Entra configuration', () => {
    expect(configuration({} as ImportMetaEnv).ready).toBe(false);
    const env = {
      BASE_URL: '/', MODE: 'test', DEV: false, PROD: false, SSR: false,
      VITE_ENTRA_TENANT_ID: TEST_RUN_ID,
      VITE_ENTRA_CLIENT_ID: TEST_REQUEST_ID,
      VITE_ENTRA_API_SCOPE: `api://${TEST_REQUEST_ID}/FloodLab.Access`,
      VITE_API_BASE_URL: 'http://127.0.0.1:8088',
    } as ImportMetaEnv;
    expect(configuration(env).ready).toBe(true);
    expect(configuration({ ...env, VITE_API_BASE_URL: 'http://nonlocal.example' }).ready).toBe(false);
    expect(configuration({ ...env, VITE_ENTRA_API_SCOPE: `api://${TEST_REQUEST_ID}/.default` }).ready).toBe(false);
    expect(configuration({ ...env, VITE_API_BASE_URL: 'https://secret:credential@example.invalid' }).ready).toBe(false);
  });

  it('treats malformed error bodies as unknown for writes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('null', { status: 502 })));
    await expect(new LabClient('http://localhost:8088', testAuth).mutate(pending))
      .rejects.toMatchObject({ outcome: 'unknown' });
  });

  it('journals only synthetic operation inputs and scopes them to the account', () => {
    writePending('TEST-ONLY-A', pending);
    expect(readPending('TEST-ONLY-A')).toEqual(pending);
    expect(readPending('TEST-ONLY-B')).toBeNull();
    expect(sessionStorage.getItem('flood-lab:pending:v1:TEST-ONLY-A')).not.toContain('TOKEN');
    writePending('TEST-ONLY-A', null);
    expect(readPending('TEST-ONLY-A')).toBeNull();
    expect(new LabError('failed', 'setup_required', 'Configure first.').outcome).toBe('failed');
  });
});
