import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { ApiError } from './api'
import { ConfigurationEditor, type ConfigurationContent } from './ConfigurationEditor'

const catalog = {
  schema_version: 'operation-catalog/v1',
  name: 'Ticket records',
  operations: [
    {
      key: 'ticket.read',
      version: '1',
      label: 'Read a ticket',
      effect: 'read',
      invocation: { kind: 'rest', method: 'GET', path: '/tickets/{ticket_id}' },
      parameters: [{ name: 'ticket_id', type: 'uuid', required: true }],
      results: [{ name: 'priority', type: 'integer', required: true }],
      recovery: 'Read-only; no target changes.',
    },
  ],
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function setup(
  onRegister = vi
    .fn<(content: ConfigurationContent) => Promise<void>>()
    .mockResolvedValue(undefined),
) {
  const reload = vi.fn().mockResolvedValue(undefined)
  const router = createMemoryRouter([
    {
      path: '/',
      element: (
        <ConfigurationEditor kind="rest" assets={[]} onRegister={onRegister} onReload={reload} />
      ),
    },
  ])
  render(<RouterProvider router={router} />)
  return { onRegister, reload }
}

async function chooseCatalog(content: unknown = catalog) {
  const file = new File([JSON.stringify(content)], 'ticket-catalog.json', {
    type: 'application/json',
  })
  Object.defineProperty(file, 'text', { value: async () => JSON.stringify(content) })
  fireEvent.change(screen.getByLabelText('Operation catalog JSON file'), {
    target: { files: [file] },
  })
  await waitFor(() =>
    expect(screen.queryByText('Reading the selected catalog...')).not.toBeInTheDocument(),
  )
}

describe('administrator configuration form', () => {
  it('starts unknown and requires inspection before registration', async () => {
    const { onRegister } = setup()
    expect(screen.getByLabelText('Target classification')).toHaveValue('unknown')
    expect(screen.getByRole('button', { name: 'Register configuration' })).toBeDisabled()
    await chooseCatalog()
    expect(screen.getByRole('heading', { name: 'Read a ticket' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Register configuration' })).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /I inspected/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Register configuration' }))
    await waitFor(() => expect(onRegister).toHaveBeenCalledOnce())
    expect(onRegister.mock.calls[0][0]).toMatchObject({
      classification: 'unknown',
      resource_id: '',
      catalog,
    })
    await screen.findByText(
      /Configuration registered. Connectivity and target permissions remain unverified/,
    )
    expect(screen.getByLabelText('Operation catalog JSON file')).toHaveValue('')
  })

  it('invalidates the acknowledgement when target metadata changes', async () => {
    setup()
    await chooseCatalog()
    fireEvent.click(screen.getByRole('checkbox', { name: /I inspected/ }))
    fireEvent.change(screen.getByLabelText('Concrete resource identity'), {
      target: { value: 'ticket-lab' },
    })
    expect(screen.getByRole('checkbox', { name: /I inspected/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Register configuration' })).toBeDisabled()
  })

  it('guards unload while a selected catalog is still being read', () => {
    setup()
    const file = new File(['{}'], 'catalog.json', { type: 'application/json' })
    Object.defineProperty(file, 'text', { value: () => new Promise<string>(() => undefined) })
    fireEvent.change(screen.getByLabelText('Operation catalog JSON file'), {
      target: { files: [file] },
    })
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
  })

  it('rejects unsupported catalog content and keeps target input', async () => {
    const { onRegister } = setup()
    fireEvent.change(screen.getByLabelText('Concrete resource identity'), {
      target: { value: 'ticket-lab' },
    })
    await chooseCatalog({ ...catalog, sql: 'select * from private' })
    expect(screen.getByRole('alert')).toHaveTextContent('unsupported field')
    expect(screen.getByLabelText('Concrete resource identity')).toHaveValue('ticket-lab')
    expect(screen.getByRole('button', { name: 'Register configuration' })).toBeDisabled()
    expect(onRegister).not.toHaveBeenCalled()
  })

  it('retains metadata and inspected catalog on conflict until confirmed reload', async () => {
    const onRegister = vi
      .fn<(content: ConfigurationContent) => Promise<void>>()
      .mockRejectedValue(new ApiError(409, 'Configuration changed', null))
    const { reload } = setup(onRegister)
    fireEvent.change(screen.getByLabelText('Concrete resource identity'), {
      target: { value: 'my-resource' },
    })
    await chooseCatalog()
    fireEvent.click(screen.getByRole('checkbox', { name: /I inspected/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Register configuration' }))
    await screen.findByRole('button', { name: 'Export my input' })
    expect(screen.getByLabelText('Concrete resource identity')).toHaveValue('my-resource')
    expect(screen.getByRole('heading', { name: 'Read a ticket' })).toBeVisible()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    fireEvent.click(screen.getByRole('button', { name: 'Reload latest version' }))
    expect(reload).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Concrete resource identity')).toHaveValue('my-resource')
  })
})
