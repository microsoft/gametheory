import { expect, test, type Page } from '@playwright/test'
import { ids, preparationFixture, serviceCatalog } from './preparation.fixture'

const boardPath = `/w/${ids.workspace}/boards/${ids.board}`
const workspacePath = `/w/${ids.workspace}`
function futureLocal(hours: number) {
  const date = new Date(Date.now() + hours * 3600000)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
function step(page: Page, index: number) {
  return page.getByRole('region', { name: `Preparation step ${index}`, exact: true })
}
async function selectCatalog(page: Page, value: unknown = serviceCatalog) {
  await page.getByLabel('Operation catalog JSON file').setInputFiles({
    name: 'service-ticket-catalog.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(value)),
  })
}

test('ordinary UI registration, publication, typed result bindings, preview, and separate preparation approval', async ({
  page,
}) => {
  test.setTimeout(60000)
  const server = await preparationFixture(page, { profile: 'admin', empty: true })
  await page.goto(workspacePath)
  await page.getByRole('button', { name: 'Connection inventory', exact: true }).click()
  await page.getByLabel('Name', { exact: true }).fill('Service ticket API')
  await page.getByRole('combobox', { name: 'Integration', exact: true }).selectOption('rest')
  await page
    .getByRole('combobox', { name: 'Environment', exact: true })
    .selectOption(ids.environment)
  await page.getByRole('button', { name: 'Add to inventory' }).click()
  await page.getByRole('link', { name: 'Configure & view history' }).click()
  await expect(page.getByLabel('Target classification')).toHaveValue('unknown')
  await page.getByLabel('Target classification').selectOption('nonproduction')
  await page.getByLabel('Concrete resource identity').fill('service-ticket-sandbox')
  await page.getByLabel('Endpoint metadata').fill('http://127.0.0.1:9050')
  await page.getByLabel('Identity reference (no credentials)').fill('fixture-service-identity')
  await selectCatalog(page)
  await expect(page.getByRole('heading', { name: 'Create a service ticket' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Register configuration' })).toBeDisabled()
  await page.getByRole('checkbox', { name: /I inspected the catalog/ }).check()
  await page.getByRole('button', { name: 'Register configuration' }).click()
  await expect(
    page.getByText('Configuration 1 registered. Live readiness is unverified.'),
  ).toBeVisible()
  expect(server.configurations()[0].content.catalog).toEqual(serviceCatalog)

  await page.getByRole('link', { name: 'Connection inventory', exact: true }).click()
  await page.getByRole('button', { name: 'Access', exact: true }).click()
  await page.getByLabel('Approver Entra object ID').fill(ids.reviewer)
  await page.getByRole('button', { name: 'Grant preparation approval' }).click()
  await expect(page.getByText('Explicit preparation approver', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Scenarios', exact: true }).click()
  await page.getByLabel('New scenario', { exact: true }).fill('Service coordination review')
  await page.getByRole('button', { name: 'Create scenario', exact: true }).click()
  await page
    .locator('[contenteditable="true"]')
    .first()
    .fill(
      'Prepare a bounded review of service ticket coordination. This is not authorization for live changes.',
    )
  await page.getByRole('button', { name: 'Save draft', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Draft saved.')
  await page.getByRole('button', { name: 'Publish revision', exact: true }).click()
  await page.getByRole('button', { name: 'Review & history', exact: true }).click()
  await page.getByText(/^Revision 1 ·/).click()
  await page.getByRole('button', { name: 'Create board', exact: true }).click()
  await page.getByLabel('New board name for revision 1').fill('Service ticket preparation')
  await page.getByRole('button', { name: 'Create board from revision 1', exact: true }).click()
  await page.getByRole('link', { name: 'Open Service ticket preparation', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Service ticket preparation', exact: true }),
  ).toBeVisible()

  await page.getByRole('button', { name: 'Add operation', exact: true }).click()
  await step(page, 1).getByLabel('Step 1 label').fill('Create ticket')
  await step(page, 1)
    .getByLabel('Registered operation and configuration')
    .selectOption({ label: 'Create a service ticket · ticket.create @ 1' })
  await step(page, 1).getByLabel('title', { exact: true }).fill('Review queue ownership')
  await page.getByRole('button', { name: 'Add operation', exact: true }).click()
  await step(page, 2).getByLabel('Step 2 label').fill('Read created ticket')
  await step(page, 2)
    .getByLabel('Registered operation and configuration')
    .selectOption({ label: 'Read a service ticket · ticket.read @ 1' })
  await step(page, 2).getByLabel('Value source for record_id').selectOption('result')
  await step(page, 2)
    .getByLabel('Prior result for record_id')
    .selectOption({ label: 'Create ticket · record_id (uuid)' })
  await page.getByRole('button', { name: 'Add bounded wait', exact: true }).click()
  await step(page, 3).getByLabel('Step 3 label').fill('Review interval')
  await step(page, 3).getByLabel('Bounded wait in seconds').fill('60')
  await page.getByRole('button', { name: 'Add condition', exact: true }).click()
  await step(page, 4).getByLabel('Step 4 label').fill('High priority branch')
  await step(page, 4)
    .getByLabel('Earlier operation result source')
    .selectOption({ label: 'Read created ticket' })
  await step(page, 4).getByLabel('Declared result field').selectOption('priority')
  await step(page, 4).getByRole('combobox', { name: 'Comparison', exact: true }).selectOption('gte')
  await step(page, 4).getByLabel('Compare result · priority').fill('6')
  await step(page, 4)
    .getByRole('checkbox', { name: 'True branch: Review interval', exact: true })
    .check()
  await page.getByRole('checkbox', { name: 'Supply an explicit proposed time window' }).check()
  await page.getByLabel('Window starts (local time)').fill(futureLocal(12))
  await page.getByLabel('Window ends (local time)').fill(futureLocal(13))
  await page
    .getByLabel('Recovery policy')
    .fill(
      'External operator review is required. Preserve evidence and human changes; do not infer rollback success.',
    )
  await page.getByRole('button', { name: 'Save preparation', exact: true }).click()
  await expect(
    page.getByText(
      'Preparation draft saved. Any material change requires a fresh preview and approval.',
    ),
  ).toBeVisible()
  const saved = server.currentBoard().draft
  expect(saved.steps?.[1].parameters?.record_id).toEqual({
    source_step_id: saved.steps?.[0].id,
    field: 'record_id',
  })
  expect(saved.steps?.[3].condition?.value).toBe(6)
  await page.getByRole('button', { name: 'Freeze saved preview', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Preview 1', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Unverified live prerequisites' })).toBeVisible()
  await expect(
    page.getByText('Prior result binding is declarative; its value has not been observed.'),
  ).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Approve preparation', exact: true }),
  ).toBeDisabled()
  expect(server.previews()[0].manifest.scenario.revision_version).toBe(1)

  server.become('reviewer')
  await page.reload()
  await expect(page.getByRole('button', { name: 'Save preparation', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Preview & review', exact: true }).click()
  await page
    .getByRole('combobox', { name: 'Preparation decision', exact: true })
    .selectOption('approved')
  await page.getByLabel('Explicit approval expiry (local time)').fill(futureLocal(24))
  await page
    .getByLabel('Reviewer note')
    .fill(
      'Reviewed the exact preparation, unresolved live prerequisites, limits, and recovery policy.',
    )
  await page.getByRole('checkbox', { name: /I reviewed this exact snapshot/ }).check()
  await page.getByRole('button', { name: 'Approve preparation', exact: true }).click()
  await expect(
    page.getByText('Preparation approved — not authorized to execute', { exact: true }),
  ).toBeVisible()
  expect(server.approvals()[0].digest).toBe(server.previews()[0].digest)
  expect(server.approvals()[0].execution_authorized).toBe(false)
  await expect(page.getByRole('button', { name: 'Execution disabled', exact: true })).toBeDisabled()
  expect(server.executeRequests()).toBe(0)

  server.become('author')
  await page.reload()
  await page.getByLabel('Board name', { exact: true }).fill('Revised service preparation')
  await page.getByRole('button', { name: 'Save preparation', exact: true }).click()
  await expect(
    page.getByText('Preparation decision no longer valid', { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'History', exact: true }).click()
  await page.getByRole('button', { name: 'Inspect preview 1' }).click()
  await expect(
    page.getByText(
      'This is not the current saved preview. A fresh preview is required before a decision.',
    ),
  ).toBeVisible()
  expect(server.previews()[0].manifest.draft.name).toBe('Service ticket preparation')
})

test('conflicting and failed board saves preserve input, export, and navigation guards', async ({
  page,
}) => {
  const server = await preparationFixture(page)
  await page.goto(boardPath)
  await page.getByLabel('Board name', { exact: true }).fill('My retained preparation')
  server.changeBoard()
  await page.getByRole('button', { name: 'Save preparation', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Export my input', exact: true })).toBeVisible()
  await expect(page.getByLabel('Board name', { exact: true })).toHaveValue(
    'My retained preparation',
  )
  page.once('dialog', (dialog) => dialog.dismiss())
  await page.getByRole('button', { name: 'Reload latest version' }).click()
  await expect(page.getByLabel('Board name', { exact: true })).toHaveValue(
    'My retained preparation',
  )
  server.failSaves()
  await page.getByRole('button', { name: 'Save preparation', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Storage unavailable')
  await page.getByRole('link', { name: 'Game boards', exact: true }).click()
  await expect(page.getByRole('alertdialog')).toBeVisible()
  await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
  await expect(page.getByLabel('Board name', { exact: true })).toHaveValue(
    'My retained preparation',
  )
})

test('invalid typed values and cyclic conditions remain editable but cannot be saved', async ({
  page,
}) => {
  await preparationFixture(page)
  await page.goto(boardPath)
  await page.getByRole('button', { name: 'Add operation', exact: true }).click()
  await step(page, 1).getByLabel('Step 1 label').fill('Update ticket')
  await step(page, 1)
    .getByLabel('Registered operation and configuration')
    .selectOption({ label: 'Update ticket capacity · ticket.update @ 1' })
  await step(page, 1).getByLabel('units', { exact: true }).fill('not a number')
  await expect(step(page, 1).getByLabel('units', { exact: true })).toHaveValue('not a number')
  await expect(page.getByRole('button', { name: 'Save preparation', exact: true })).toBeDisabled()
  await step(page, 1).getByLabel('units', { exact: true }).fill('5')
  await page.getByRole('button', { name: 'Add bounded wait', exact: true }).click()
  await step(page, 2).getByLabel('Step 2 label').fill('Wait')
  await step(page, 2).getByLabel('Bounded wait in seconds').fill('86401')
  await expect(page.getByRole('alert', { name: 'Preparation validation' })).toContainText(
    'bounded wait',
  )
  await step(page, 2).getByLabel('Bounded wait in seconds').fill('60')
  await step(page, 1).getByText('Explicit dependencies (0)', { exact: true }).click()
  await step(page, 1).getByRole('checkbox', { name: 'Depends on: Wait' }).check()
  await step(page, 2).getByText('Explicit dependencies (0)', { exact: true }).click()
  await step(page, 2).getByRole('checkbox', { name: 'Depends on: Update ticket' }).check()
  await expect(page.getByRole('alert', { name: 'Preparation validation' })).toContainText('cycle')
  await expect(page.getByRole('button', { name: 'Save preparation', exact: true })).toBeDisabled()
})

test('viewer is read-only and neither organization admin nor contributing grantee gets automatic review', async ({
  page,
}) => {
  const server = await preparationFixture(page, { profile: 'viewer' })
  server.approvedPreview()
  await page.goto(boardPath)
  await expect(page.getByLabel('Board name', { exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Add operation', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Preview & review', exact: true }).click()
  await expect(
    page.getByRole('combobox', { name: 'Preparation decision', exact: true }),
  ).toBeDisabled()
  server.become('admin')
  await page.reload()
  await page.getByRole('button', { name: 'Preview & review', exact: true }).click()
  await expect(
    page.getByRole('combobox', { name: 'Preparation decision', exact: true }),
  ).toBeDisabled()
  await expect(
    page.getByText('No explicit workspace approver grant.', { exact: true }),
  ).toBeVisible()
  server.giveAuthorGrant()
  server.become('author')
  await page.reload()
  await page.getByRole('button', { name: 'Preview & review', exact: true }).click()
  await expect(
    page.getByRole('combobox', { name: 'Preparation decision', exact: true }),
  ).toBeDisabled()
  await expect(
    page.getByText(
      'You created or contributed to this board and cannot approve or reject its preparation.',
    ),
  ).toBeVisible()
})

test('explicit expiry, rejection, decision revocation, and grant revocation stay distinct', async ({
  page,
}) => {
  const server = await preparationFixture(page, { profile: 'reviewer' })
  server.become('author')
  server.approvedPreview(new Date(Date.now() - 60000).toISOString())
  server.become('reviewer')
  await page.goto(boardPath)
  await expect(page.getByText('Preparation decision expired', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Preview & review', exact: true }).click()
  await page
    .getByRole('combobox', { name: 'Preparation decision', exact: true })
    .selectOption('rejected')
  await page.getByLabel('Explicit approval expiry (local time)').fill(futureLocal(24))
  await page
    .getByLabel('Reviewer note')
    .fill('Missing inputs need another review before preparation approval.')
  await page.getByRole('checkbox', { name: /I reviewed this exact snapshot/ }).check()
  await page.getByRole('button', { name: 'Reject preparation', exact: true }).click()
  await expect(page.getByText('Preparation rejected', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'History', exact: true }).click()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'Revoke decision 2', exact: true }).click()
  await expect(
    page.getByText('Preparation decision revoked', { exact: true }).first(),
  ).toBeVisible()
  server.become('admin')
  await page.goto(`${workspacePath}?section=access`)
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'Revoke approver capability', exact: true }).click()
  await expect(
    page.getByText(/Approver capability revoked\. Decision history is retained/),
  ).toBeVisible()
  server.become('reviewer')
  await page.goto(boardPath)
  await page.getByRole('button', { name: 'Preview & review', exact: true }).click()
  await expect(
    page.getByRole('combobox', { name: 'Preparation decision', exact: true }),
  ).toBeDisabled()
})

test('catalog rejects SQL, credentials, remote references, and duplicate JSON properties', async ({
  page,
}) => {
  await preparationFixture(page, { profile: 'admin' })
  await page.goto(`/w/${ids.workspace}/connections/${ids.connection}`)
  await page.getByLabel('Concrete resource identity').fill('Retained-resource')
  await selectCatalog(page, { ...serviceCatalog, $ref: 'https://example.test/remote' })
  await expect(page.getByRole('alert')).toContainText('unsupported field')
  await expect(page.getByLabel('Concrete resource identity')).toHaveValue('Retained-resource')
  await page.getByLabel('Operation catalog JSON file').setInputFiles({
    name: 'duplicate.json',
    mimeType: 'application/json',
    buffer: Buffer.from(
      '{"schema_version":"operation-catalog/v1","name":"one","name":"two","operations":[]}',
    ),
  })
  await expect(page.getByRole('alert')).toContainText('Duplicate JSON property')
  await selectCatalog(page)
  await page.getByLabel('Endpoint metadata').fill('https://user:credential@example.test')
  await expect(page.getByRole('alert')).toContainText('without credentials')
  await expect(
    page.getByRole('button', { name: 'Register configuration', exact: true }),
  ).toBeDisabled()
})

test('configuration history is inspectable by viewers and withdrawal preserves immutable revisions', async ({
  page,
}) => {
  const server = await preparationFixture(page, { profile: 'viewer' })
  await page.goto(`/w/${ids.workspace}/connections/${ids.connection}`)
  await expect(
    page.getByRole('heading', { name: 'Register a configuration revision' }),
  ).toHaveCount(0)
  await page.getByText(/Configuration 1 · Service ticket operations/).click()
  await expect(page.getByText('service-fixture', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Withdraw configuration 1' })).toHaveCount(0)
  server.become('admin')
  await page.reload()
  await page.getByText(/Configuration 1 · Service ticket operations/).click()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'Withdraw configuration 1' }).click()
  await expect(page.getByText('Withdrawn', { exact: true })).toBeVisible()
  expect(server.configurations()[0].content.catalog).toEqual(serviceCatalog)
  await expect(page.getByRole('button', { name: 'Export immutable configuration' })).toBeVisible()
})

test('Graph registration pins a fixed template, sender, explicit recipients, and trusted link without sending', async ({
  page,
}) => {
  const server = await preparationFixture(page, { profile: 'admin', empty: true })
  await page.goto(`${workspacePath}?section=connections`)
  await page.getByLabel('Name', { exact: true }).fill('Fixed service notifications')
  await page.getByRole('combobox', { name: 'Integration', exact: true }).selectOption('graph')
  await page
    .getByRole('combobox', { name: 'Environment', exact: true })
    .selectOption(ids.environment)
  await page.getByRole('button', { name: 'Add to inventory' }).click()
  await page.getByRole('link', { name: 'Configure & view history' }).click()
  await page.getByLabel('Concrete resource identity').fill('service-notification-policy')
  await page.getByLabel('Endpoint metadata').fill('https://mail.example.test')
  await page.getByLabel('Identity reference (no credentials)').fill('fixture-mail-identity')
  await page.getByLabel('Immutable template asset version').selectOption(ids.template)
  await page.getByLabel('Fixed sender mailbox').fill('coordinator@example.test')
  await page
    .getByLabel('Explicit recipient mailboxes')
    .fill('reviewer@example.test\nobserver@example.test')
  await page.getByLabel('Trusted operational-app link').fill('http://127.0.0.1:9050/tasks')
  await selectCatalog(page, {
    schema_version: 'operation-catalog/v1',
    name: 'Fixed service notices',
    operations: [
      {
        key: 'notice.fixed',
        version: '1',
        label: 'Prepare fixed service notice',
        effect: 'notify',
        invocation: { kind: 'graph', template_key: 'service-notice' },
        parameters: [{ name: 'record_id', type: 'uuid', required: true }],
        results: [],
        recovery: 'No message recall claim; retain external audit evidence.',
      },
    ],
  })
  await page.getByRole('checkbox', { name: /I inspected the catalog/ }).check()
  await page.getByRole('button', { name: 'Register configuration' }).click()
  await expect(
    page.getByText('Configuration 1 registered. Live readiness is unverified.'),
  ).toBeVisible()
  expect(server.configurations()[0].content.notification).toEqual({
    template_asset_id: ids.template,
    sender: 'coordinator@example.test',
    recipients: ['reviewer@example.test', 'observer@example.test'],
    trusted_link: 'http://127.0.0.1:9050/tasks',
  })
  await page.getByText('Configuration 1 · Fixed service notices', { exact: true }).click()
  await expect(page.getByText('reviewer@example.test', { exact: true })).toBeVisible()
  await expect(page.getByText('Unverified. No message is sent by this application.')).toBeVisible()
  expect(server.executeRequests()).toBe(0)
})

test('denied approver grants retain the proposed identity and guard navigation', async ({
  page,
}) => {
  const server = await preparationFixture(page, { profile: 'admin' })
  await page.goto(`${workspacePath}?section=access`)
  await page.getByLabel('Approver Entra object ID').fill(ids.viewer)
  server.become('author')
  await page.getByRole('button', { name: 'Grant preparation approval' }).click()
  await expect(page.getByRole('alert')).toContainText('Organization administrator required')
  await expect(page.getByLabel('Approver Entra object ID')).toHaveValue(ids.viewer)
  await page.getByRole('button', { name: 'Game boards', exact: true }).click()
  await expect(page.getByRole('alertdialog')).toBeVisible()
  await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
  await expect(page.getByLabel('Approver Entra object ID')).toHaveValue(ids.viewer)
})

test('denied and unavailable-service states do not imply empty successful preparation', async ({
  page,
}) => {
  await preparationFixture(page, { denied: true })
  await page.goto(boardPath)
  await expect(page.getByRole('alert')).toContainText('Workspace access denied')
  await expect(page.getByRole('button', { name: 'Freeze saved preview' })).toHaveCount(0)
})

test('missing configuration service keeps draft input and disables preview', async ({ page }) => {
  const server = await preparationFixture(page)
  server.loseConfigurations()
  await page.goto(boardPath)
  await page.getByLabel('Board name', { exact: true }).fill('Keep this preparation')
  await expect(page.getByRole('alert')).toContainText('Configuration storage unavailable')
  await expect(page.getByText(/do not treat this as an empty catalog/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Freeze saved preview' })).toBeDisabled()
  await expect(page.getByLabel('Board name', { exact: true })).toHaveValue('Keep this preparation')
})

test('revoked connection access hides cached immutable snapshots while keeping the owned draft editable', async ({
  page,
}) => {
  const server = await preparationFixture(page)
  await page.goto(boardPath)
  await page.getByRole('button', { name: 'Add operation', exact: true }).click()
  await step(page, 1).getByLabel('Step 1 label').fill('Read service record')
  await step(page, 1).getByLabel('Registered operation and configuration').selectOption({
    label: 'Read a service ticket · ticket.read @ 1',
  })
  await page.getByRole('button', { name: 'Save preparation', exact: true }).click()
  await page.getByRole('button', { name: 'Freeze saved preview', exact: true }).click()
  await page.getByText('Service ticket API · configuration 1', { exact: true }).click()
  await expect(page.getByText('service-fixture', { exact: true })).toBeVisible()
  server.revokeConnectionAccess()
  await page.getByRole('button', { name: 'Refresh review access', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Preview content is unavailable' })).toBeVisible()
  await expect(page.getByText('service-fixture', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Export immutable preview' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Preparation', exact: true }).click()
  await expect(page.getByLabel('Board name', { exact: true })).toBeEnabled()
  await step(page, 1).getByLabel('Registered operation and configuration').selectOption('')
  await page.getByRole('button', { name: 'Save preparation', exact: true }).click()
  await expect(
    page.getByText(
      'Preparation draft saved. Any material change requires a fresh preview and approval.',
    ),
  ).toBeVisible()
})

for (const width of [1440, 390]) {
  for (const dark of [false, true]) {
    test(`preparation surface ${width}px ${dark ? 'dark' : 'light'}`, async ({ page }) => {
      const errors: string[] = []
      page.on('pageerror', (error) => errors.push(error.message))
      const server = await preparationFixture(page)
      server.approvedPreview()
      await page.setViewportSize({ width, height: 1000 })
      await page.goto(boardPath)
      if (dark) await page.getByRole('button', { name: 'Use dark theme' }).click()
      for (const pane of ['Preparation', 'Preview & review', 'History']) {
        await page.getByRole('button', { name: pane, exact: true }).click()
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        ).toBeTruthy()
      }
      await page.getByRole('button', { name: 'Preview & review', exact: true }).click()
      await page.screenshot({
        path: `test-results/preparation-${width}-${dark ? 'dark' : 'light'}.png`,
        fullPage: true,
      })
      expect(errors).toEqual([])
    })
  }
}
