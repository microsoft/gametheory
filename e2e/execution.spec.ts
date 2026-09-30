import { expect, test, type Page } from '@playwright/test'

const environment = '10000000-0000-4000-8000-000000000001'
const production = '10000000-0000-4000-8000-000000000002'
const actor = '10000000-0000-4000-8000-000000000003'
const workspace = '10000000-0000-4000-8000-000000000004'

async function settingsFixture(page: Page, admin = true) {
  const policies = [
    {
      environment_id: environment,
      name: 'Training lab',
      classification: 'nonproduction',
      execution_enabled: false,
      approval_required: false,
      version: 1,
      updated_by: actor,
      updated_at: '2026-09-22T12:00:00Z',
    },
    {
      environment_id: production,
      name: 'Production',
      classification: 'production',
      execution_enabled: false,
      approval_required: true,
      version: 1,
      updated_by: actor,
      updated_at: '2026-09-22T12:00:00Z',
    },
  ]
  const requests: string[] = []
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    requests.push(path)
    let body: unknown
    let headers: Record<string, string> = {}
    if (path === '/api/me') body = { object_id: actor, organization_admin: admin }
    else if (path === '/api/workspaces')
      body = [{ id: workspace, name: 'Exercise operations', role: 'owner' }]
    else if (path === '/api/admin/runtime')
      body = {
        execution_enabled: false,
        sql_configured: true,
        scheduler_configured: false,
        target_bindings_configured: false,
        run_assistant_enabled: false,
        message:
          'Configuration is not live readiness. This is a UI fixture, not a deployed environment.',
      }
    else if (path === '/api/admin/environment-policies') body = policies
    else if (path.startsWith('/api/admin/environment-policies/')) {
      const policy = policies.find((item) => path.includes(item.environment_id))
      if (!policy) throw new Error('Unknown fixture environment')
      if (request.method() === 'PUT') {
        expect(request.headers()['if-match']).toBe(`"${policy.version}"`)
        const input = request.postDataJSON()
        if (policy.classification === 'production') expect(input.approval_required).toBe(true)
        Object.assign(policy, input, { version: policy.version + 1 })
      }
      headers = { ETag: `"${policy.version}"` }
      body = path.endsWith('/history') ? [policy] : policy
    } else if (path.endsWith('/execution-grants')) body = []
    else throw new Error(`Unhandled settings fixture: ${request.method()} ${path}`)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers,
      body: JSON.stringify(body),
    })
  })
  return requests
}

test('admin configures nonproduction approval and sees the locked production rule', async ({
  page,
}, info) => {
  await settingsFixture(page)
  await page.setViewportSize({ width: 1360, height: 1000 })
  await page.goto('/settings')
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()
  await page.getByRole('button', { name: /Production.*approval required/ }).click()
  await expect(page.getByLabel('Require independent execution approval')).toBeChecked()
  await expect(page.getByLabel('Require independent execution approval')).toBeDisabled()
  await expect(page.getByLabel('Environment classification')).toBeDisabled()
  await page.screenshot({ path: info.outputPath('settings-desktop.png'), fullPage: true })
  await page.getByRole('button', { name: /Training lab/ }).click()
  await expect(page.getByLabel('Require independent execution approval')).not.toBeChecked()
  await page.getByLabel('Require independent execution approval').check()
  await page.getByRole('button', { name: 'Save environment policy' }).click()
  await expect(page.getByText(/Policy saved/)).toBeVisible()
  await expect(page.getByText('Policy version 2')).toBeVisible()
  await page.getByRole('button', { name: 'Policy history' }).click()
  await expect(page.getByText('Version 2', { exact: true })).toBeVisible()
})

test('settings remains usable on a narrow screen and in the dark theme', async ({ page }, info) => {
  await settingsFixture(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/settings')
  await page.getByRole('button', { name: /Training lab/ }).click()
  await page.getByRole('button', { name: 'Use dark theme' }).click()
  await expect(page.getByLabel('Require independent execution approval')).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: info.outputPath('settings-mobile-dark.png'), fullPage: true })
  await page.getByLabel('Require independent execution approval').focus()
  await page.keyboard.press('Space')
  await expect(page.getByLabel('Require independent execution approval')).toBeChecked()
})

test('nonadmins cannot open policy management', async ({ page }) => {
  const requests = await settingsFixture(page, false)
  await page.goto('/settings')
  await expect(page.getByText(/Organization administrator access is required/)).toBeVisible()
  expect(requests.filter((path) => path.startsWith('/api/admin/'))).toEqual([])
})

test('run view keeps an uncertain effect distinct from success', async ({ page }, info) => {
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body =
      path === '/api/me'
        ? { object_id: actor, organization_admin: false }
        : {
            id: 'fixture-run',
            board_id: 'fixture-board',
            version: 4,
            state: 'stopped_incomplete',
            phase: 'exercise',
            operator: actor,
            created_at: '2026-09-22T12:00:00Z',
            manifest_digest: 'a'.repeat(64),
            context_id: 'fixture-context',
            approval_required: false,
            approval_status: 'not_required',
            blockers: ['An accepted SQL operation has no confirmed receipt.'],
            can_operate: true,
            can_stop: true,
            can_review: false,
            manifest: {
              trigger: 'manual',
              recovery: [],
              observations: [],
              objectives: [],
              preparation: {
                draft: {
                  name: 'Test-only operational exercise',
                  steps: [{ id: 'step', label: 'Update exercise record' }],
                },
                scenario: { content: { objectives: [] } },
                configurations: [],
              },
            },
            steps: [
              {
                step_id: 'step',
                phase: 'exercise',
                state: 'unknown',
                result: {},
                reason: 'The effect may have committed. Reconcile the existing operation identity.',
                samples: 1,
                started_at: '2026-09-22T12:00:00Z',
                finished_at: null,
              },
            ],
            events: [
              {
                id: 'fixture-event',
                kind: 'operation.unknown',
                step_id: 'step',
                created_at: '2026-09-22T12:00:10Z',
                detail: {
                  phase: 'exercise',
                  reason: 'No confirmed outcome after process interruption.',
                },
              },
            ],
            findings: [],
          }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: '"4"' },
      body: JSON.stringify(body),
    })
  })
  await page.setViewportSize({ width: 1360, height: 1000 })
  await page.goto(`/w/${workspace}/runs/fixture-run`)
  await expect(page.getByText('stopped incomplete', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Run checklist' })).toBeVisible()
  await expect(
    page.getByText('Enter an operator note in Operator controls to record this report.'),
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Start run', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'External outcome report' })).toBeVisible()
  await page.screenshot({ path: info.outputPath('run-intervention.png'), fullPage: true })
})
