import { expect, test, type Page } from '@playwright/test'
import { runIds, runSetupFixture } from './run-setup.fixture'

async function openRunSetup(page: Page) {
  await page.goto(`/w/${runIds.workspace}/boards/${runIds.board}`)
  await page.getByRole('button', { name: 'Exercise runs' }).click()
  await expect(page.getByRole('heading', { name: 'Set up an exercise run' })).toBeVisible()
}

test('operator builds and creates a guided run without typing JSON or IDs', async ({
  page,
}, info) => {
  const fixture = await runSetupFixture(page, {
    blockers: [
      {
        code: 'readiness_missing',
        message:
          'Resource ticket API: No current operator readiness receipt covering this target and window',
        remedy: 'readiness',
        configuration_id: runIds.restConfiguration,
        environment_id: runIds.environment,
      },
    ],
  })
  await page.setViewportSize({ width: 1360, height: 1000 })
  await openRunSetup(page)

  await page.getByRole('button', { name: 'Add a check' }).click()
  const watch = page.getByRole('group', { name: 'Check 1' })
  await expect(watch.getByLabel('Read step')).toHaveValue(runIds.readOccupancy)
  await watch.getByLabel('Reading', { exact: true }).selectOption('occupancy_percent')
  await watch.getByLabel('Continue when the reading').selectOption('gt')
  await watch.getByLabel('Compare with · occupancy_percent').fill('85')
  await expect(
    watch.getByText(
      /Every 10 seconds, read “Read occupancy” until occupancy_percent is greater than 85/,
    ),
  ).toBeVisible()

  const breach = page.getByRole('group', { name: 'Breach identified quickly' })
  await breach.getByLabel('Measure this goal from run evidence').check()
  await breach.getByLabel('Evidence step').selectOption(runIds.readOccupancy)
  await breach.getByLabel('Evidence result').selectOption('occupancy_percent')
  await breach.getByLabel('Met when the result').selectOption('gt')
  await breach.getByLabel('Value that counts as met · occupancy_percent').fill('85')
  await breach.getByLabel('It must happen within a time limit').check()
  await breach.getByLabel('Time limit', { exact: true }).fill('2')
  await breach.getByLabel('Time limit unit').selectOption('minutes')
  await breach.getByLabel('Starting from').selectOption(`${runIds.raise}/committed_at`)

  const acknowledged = page.getByRole('group', { name: 'Ticket acknowledged' })
  await acknowledged.getByLabel('Measure this goal from run evidence').check()
  await acknowledged.getByLabel('Evidence step').selectOption(runIds.readTicket)
  await acknowledged.getByLabel('Evidence result').selectOption('acknowledged')
  await acknowledged.getByLabel('Value that counts as met · acknowledged').selectOption('true')
  await acknowledged.getByLabel('It must happen within a time limit').check()
  await acknowledged.getByLabel('Time limit', { exact: true }).fill('10')
  await acknowledged.getByLabel('Time limit unit').selectOption('minutes')
  await acknowledged.getByLabel('Starting from').selectOption(`${runIds.openTicket}/created_at`)
  await acknowledged.getByLabel('The time the system itself recorded').check()
  await acknowledged.getByLabel('Recorded time', { exact: true }).selectOption('acknowledged_at')

  const undo = page.getByRole('group', { name: 'Open resource ticket' })
  await undo.getByLabel('Undo automatically with a registered operation').check()
  await undo.getByLabel('Undo operation').selectOption(`${runIds.restConfiguration}/ticket.close/1`)
  await expect(undo.getByLabel('Ownership check')).toHaveValue('record_id')
  await expect(undo.getByLabel('Version check')).toHaveValue('expected_version')

  await expect(page.getByRole('button', { name: 'Create pinned run' })).toBeDisabled()
  await expect(page.getByText('Check the setup first.')).toBeVisible()
  await page.getByRole('button', { name: 'Check setup' }).click()
  await expect(page.getByText(/This setup is valid, so you can create the run now/)).toBeVisible()
  await expect(page.getByText(/record a readiness receipt/)).toBeVisible()
  await page.screenshot({ path: info.outputPath('run-setup-desktop.png'), fullPage: true })

  await page.getByRole('button', { name: 'Create pinned run' }).click()
  await expect(page.getByText('Run created and pinned.')).toBeVisible()
  const [checked, created] = fixture.requests.filter((item) => item.path.includes('/runs'))
  expect(checked.path).toMatch(/\/runs\/preflight$/)
  expect(checked.ifMatch).toBe('"3"')
  expect(created.ifMatch).toBe('"3"')
  expect(created.body).toEqual(checked.body)
  expect(created.body).toEqual({
    preview_id: runIds.preview,
    preview_digest: 'e'.repeat(64),
    trigger: 'manual',
    observations: [
      {
        step_id: runIds.readOccupancy,
        field: 'occupancy_percent',
        operator: 'gt',
        value: 85,
        interval_seconds: 10,
        timeout_seconds: 600,
        max_samples: 60,
      },
    ],
    objectives: [
      {
        objective_id: runIds.breach,
        step_id: runIds.readOccupancy,
        field: 'occupancy_percent',
        operator: 'gt',
        value: 85,
        anchor_step_id: runIds.raise,
        anchor_field: 'committed_at',
        within_seconds: 120,
      },
      {
        objective_id: runIds.acknowledged,
        step_id: runIds.readTicket,
        field: 'acknowledged',
        operator: 'eq',
        value: true,
        anchor_step_id: runIds.openTicket,
        anchor_field: 'created_at',
        within_seconds: 600,
        source_time_field: 'acknowledged_at',
      },
    ],
    recovery: [
      {
        step_id: runIds.openTicket,
        binding: {
          configuration_id: runIds.restConfiguration,
          operation_key: 'ticket.close',
          operation_version: '1',
        },
        parameters: {
          record_id: { source_step_id: runIds.openTicket, field: 'record_id' },
          run_id: { source_step_id: runIds.openTicket, field: 'run_id' },
          expected_version: { source_step_id: runIds.openTicket, field: 'record_version' },
        },
        ownership_parameter: 'record_id',
        version_parameter: 'expected_version',
      },
    ],
  })
  expect(fixture.unhandled).toEqual([])
})

test('run setup flags a typed SQL idempotency key and survives switching tabs', async ({
  page,
}, info) => {
  const fixture = await runSetupFixture(page, { idempotencyLiteral: true })
  await page.setViewportSize({ width: 390, height: 900 })
  await openRunSetup(page)
  await page.getByRole('button', { name: 'Use dark theme' }).click()
  await expect(page.getByText(/“Raise occupancy” has a typed idempotency_key/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create pinned run' })).toBeDisabled()
  await page.getByRole('button', { name: 'Add a check' }).click()
  await page
    .getByRole('group', { name: 'Check 1' })
    .getByLabel('Reading', { exact: true })
    .selectOption('occupancy_percent')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: info.outputPath('run-setup-mobile-dark.png'), fullPage: true })
  await page.getByRole('button', { name: 'Open the Preparation tab' }).first().click()
  await expect(page.getByRole('heading', { name: 'Preparation draft' })).toBeVisible()
  await page.getByRole('button', { name: 'Exercise runs' }).click()
  await expect(
    page.getByRole('group', { name: 'Check 1' }).getByLabel('Reading', { exact: true }),
  ).toHaveValue('occupancy_percent')
  expect(fixture.requests.filter((item) => item.path.includes('/runs'))).toEqual([])
  expect(fixture.unhandled).toEqual([])
})
