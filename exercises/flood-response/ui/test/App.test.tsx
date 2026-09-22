import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { LabError, type PendingMutation } from '../src/api';
import { readPending } from '../src/journal';
import { TestOnlyOperations, testAuth, testRecord } from './fixtures';

async function selectRequest() {
  await userEvent.click(await screen.findByRole('button', { name: /baseline blanket request/ }));
}

describe('independent operations UI', () => {
  it('fails closed when authentication configuration is missing', () => {
    render(<App auth={null} operations={null} />);
    expect(screen.getByRole('heading', { name: 'Setup required' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /sign in/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Notification sending is disabled/)).toBeInTheDocument();
  });

  it('signs in through the provided production authentication boundary', async () => {
    const auth = { ...testAuth, initialize: vi.fn().mockResolvedValue(null), signIn: vi.fn(testAuth.signIn) };
    render(<App auth={auth} operations={new TestOnlyOperations()} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in with Microsoft' }));
    expect(auth.signIn).toHaveBeenCalledOnce();
    expect(await screen.findByLabelText('Exercise run')).toBeInTheDocument();
  });

  it('lists authorized requests, acknowledges and records a typed allocation', async () => {
    const operations = new TestOnlyOperations();
    render(<App auth={testAuth} operations={operations} />);
    await selectRequest();
    await userEvent.click(screen.getByRole('button', { name: 'Acknowledge request' }));
    await screen.findByRole('heading', { name: 'Allocate resources' });
    expect(operations.calls[0].body.expected_version).toBe(testRecord.record_version);
    expect(readPending('test-only-account')).toBeNull();
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '40' } });
    fireEvent.change(screen.getByLabelText('Available at (UTC)'), { target: { value: '2030-01-01T10:15' } });
    await userEvent.click(screen.getByRole('button', { name: 'Record allocation' }));
    await screen.findByText(/requested quantity is fully allocated/);
    expect(operations.calls[1].body.quantity).toBe(40);
    expect(operations.calls[1].body.available_at).toBe('2030-01-01T10:15:00Z');
    await userEvent.click(screen.getByRole('button', { name: 'Read events' }));
    expect(await screen.findByText('request.allocate')).toBeInTheDocument();
  });

  it('preserves allocation input after a version conflict and refresh', async () => {
    const operations = new TestOnlyOperations();
    operations.current = { ...testRecord, status: 'acknowledged' };
    vi.spyOn(operations, 'mutate').mockRejectedValue(new LabError(
      'rejected', 'version_conflict', 'The request changed. Read it again.', 'TEST-ONLY-CORRELATION',
    ));
    render(<App auth={testAuth} operations={operations} />);
    await selectRequest();
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '7' } });
    await userEvent.click(screen.getByRole('button', { name: 'Record allocation' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This request changed');
    expect(screen.getByLabelText('Quantity')).toHaveValue(7);
    await userEvent.click(screen.getByRole('button', { name: 'Read latest record' }));
    expect(await screen.findByText(/Your allocation inputs are preserved/)).toBeInTheDocument();
    expect(screen.getByLabelText('Quantity')).toHaveValue(7);
  });

  it('retains an unknown operation across reload and reconciles the identical payload', async () => {
    const operations = new TestOnlyOperations();
    const normalMutation = operations.mutate.bind(operations);
    let original: PendingMutation | undefined;
    const mutate = vi.spyOn(operations, 'mutate').mockImplementationOnce(async (pending) => {
      original = structuredClone(pending);
      throw new LabError('unknown', 'network_unavailable', 'No confirmed response.');
    }).mockImplementation(normalMutation);
    const first = render(<App auth={testAuth} operations={operations} />);
    await selectRequest();
    await userEvent.click(screen.getByRole('button', { name: 'Acknowledge request' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Result unknown');
    expect(readPending('test-only-account')).toEqual(original);
    expect(screen.getByLabelText('Exercise run')).toBeDisabled();
    first.unmount();
    render(<App auth={testAuth} operations={operations} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Reconcile original operation' }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(2));
    expect(mutate.mock.calls[1][0]).toEqual(original);
    await waitFor(() => expect(readPending('test-only-account')).toBeNull());
  });

  it('keeps observers read only and never presents seed/reset/approval controls', async () => {
    const operations = new TestOnlyOperations();
    operations.role = 'observer';
    render(<App auth={testAuth} operations={operations} />);
    await selectRequest();
    expect(screen.getByText(/Read-only access/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Acknowledge request' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /seed|reset|approve|send email/i })).not.toBeInTheDocument();
  });

  it('does not erase an unknown original outcome when a retry loses authorization', async () => {
    const operations = new TestOnlyOperations();
    vi.spyOn(operations, 'mutate')
      .mockRejectedValueOnce(new LabError('unknown', 'network_unavailable', 'No response.'))
      .mockRejectedValueOnce(new LabError('rejected', 'authentication_required', 'Sign in again.'));
    render(<App auth={testAuth} operations={operations} />);
    await selectRequest();
    await userEvent.click(screen.getByRole('button', { name: 'Acknowledge request' }));
    const retry = await screen.findByRole('button', { name: 'Reconcile original operation' });
    await waitFor(() => expect(retry).toBeEnabled());
    const original = readPending('test-only-account');
    await userEvent.click(retry);
    expect(await screen.findByText(/The original result is still unknown/)).toBeInTheDocument();
    expect(readPending('test-only-account')).toEqual(original);
  });

  it('labels absent evidence indeterminate instead of participant failure', async () => {
    render(<App auth={testAuth} operations={new TestOnlyOperations()} />);
    await selectRequest();
    await userEvent.click(screen.getByRole('button', { name: 'Read events' }));
    expect(await screen.findByText(/No matching evidence.*indeterminate/)).toBeInTheDocument();
  });
});
