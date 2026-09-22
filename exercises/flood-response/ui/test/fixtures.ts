// TEST ONLY identity/network fixture. Not imported by any production entry point.
import type { Authentication } from '../src/auth';
import type {
  LabEvent, MutationResult, Operations, PendingMutation, ResourceRequest, Run,
} from '../src/api';

export const TEST_RUN_ID = '11111111-1111-5111-8111-111111111111';
export const TEST_REQUEST_ID = '22222222-2222-5222-8222-222222222222';
export const testRecord: ResourceRequest = {
  request_id: TEST_REQUEST_ID,
  sequence: 1,
  run_id: TEST_RUN_ID,
  shelter_id: '33333333-3333-5333-8333-333333333333',
  resource_type: 'blankets',
  quantity_requested: 40,
  quantity_allocated: 0,
  status: 'open',
  summary: 'TEST ONLY — baseline blanket request for Aster Reach',
  needed_by: '2030-01-01T10:20:00Z',
  created_at: '2030-01-01T10:00:00Z',
  acknowledged_at: null,
  record_version: 'v1:44444444444454448444444444444444',
};

export const testAuth: Authentication = {
  async initialize() { return { displayName: 'TEST ONLY participant', accountId: 'test-only-account' }; },
  async signIn() { return { displayName: 'TEST ONLY participant', accountId: 'test-only-account' }; },
  async signOut() {},
  async token() { return 'TEST-ONLY-NOT-A-REAL-TOKEN'; },
};

export class TestOnlyOperations implements Operations {
  current: ResourceRequest = { ...testRecord };
  role: Run['role'] = 'participant';
  eventsRead: LabEvent[] = [];
  calls: PendingMutation[] = [];

  async runs() {
    return {
      items: [{ run_id: TEST_RUN_ID, name: 'TEST ONLY Riverwatch', status: 'active', role: this.role }],
      next_offset: null,
    };
  }
  async requests() { return { items: [{ ...this.current }], next_after: null }; }
  async request() { return { ...this.current }; }
  async events() { return { items: [...this.eventsRead], next_after: null }; }
  async mutate(pending: PendingMutation): Promise<MutationResult> {
    this.calls.push(pending);
    this.current = {
      ...this.current,
      status: pending.operation === 'acknowledge' ? 'acknowledged' : 'fulfilled',
      acknowledged_at: '2030-01-01T10:05:00Z',
      quantity_allocated: pending.operation === 'allocate' ? (pending.body.quantity ?? 0) : 0,
      record_version: `v1:${pending.operation === 'acknowledge' ? '5' : '6'}4444444444454448444444444444444`,
    };
    const event: LabEvent = {
      durable_event_id: pending.operation === 'acknowledge'
        ? '77777777-7777-5777-8777-777777777771'
        : '77777777-7777-5777-8777-777777777772',
      sequence: 2,
      run_id: TEST_RUN_ID,
      operation: `request.${pending.operation}`,
      outcome: 'succeeded',
      record_id: TEST_REQUEST_ID,
      record_version: this.current.record_version,
      committed_at: '2030-01-01T10:05:00Z',
      correlation_id: '88888888-8888-5888-8888-888888888888',
      data: { test_only: true },
    };
    this.eventsRead.push(event);
    return {
      ...this.current,
      outcome: 'succeeded',
      durable_event_id: event.durable_event_id,
      committed_at: event.committed_at,
      correlation_id: event.correlation_id,
      allocation_id: pending.operation === 'allocate' ? '99999999-9999-5999-8999-999999999999' : null,
    };
  }
}
