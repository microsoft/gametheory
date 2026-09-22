import { expect, test, type Page } from '@playwright/test'
import type { Asset, Planning, Scenario } from '../apps/web/src/types'

const wid = '11111111-1111-4111-8111-111111111111'
const sid = '22222222-2222-4222-8222-222222222222'
const nodeId = '33333333-3333-4333-8333-333333333333'

async function fixture(page: Page, withImage = false) {
  let scenario: Scenario = {
    id: sid,
    workspace_id: wid,
    version: 1,
    updated_at: '2026-01-01T00:00:00Z',
    content: {
      schema_version: 1,
      title: 'Regional flood response',
      document: {
        type: 'doc',
        content: [
          {
            type: 'heading',
            attrs: { level: 2 },
            content: [{ type: 'text', text: 'Purpose and operating context' }],
          },
          {
            type: 'paragraph',
            content: [
              {
                type: 'text',
                text: 'Exercise cross-agency coordination while protecting continuity of essential services.',
              },
            ],
          },
          { type: 'reference', attrs: { kind: 'flow', targetId: 'scenario' } },
        ],
      },
      objectives: [
        {
          id: '44444444-4444-4444-8444-444444444444',
          title: 'Coordinate resource decisions',
          criterion:
            'Collect timestamped acknowledgements and evidence of a shared operating picture.',
        },
      ],
      nodes: [
        {
          id: nodeId,
          label: 'Occupancy above 85%',
          kind: 'condition',
          detail: 'Check the approved occupancy threshold.',
          position: { x: 100, y: 100 },
        },
      ],
      edges: [],
      asset_ids: [],
    },
  }
  let requests: Planning[] = []
  const aid = '55555555-5555-4555-8555-555555555555'
  const assets: Asset[] = withImage
    ? [
        {
          id: aid,
          name: 'Situation map',
          media_type: 'image/png',
          sha256: 'fixture-checksum',
          size: 68,
          state: 'ready',
          previous_id: null,
          actor: 'fixture',
          created_at: '2026-01-01T00:00:00Z',
        },
      ]
    : []
  if (withImage) {
    scenario.content.asset_ids = [aid]
    const documentNodes = scenario.content.document.content
    if (Array.isArray(documentNodes))
      documentNodes.push({ type: 'reference', attrs: { kind: 'asset', targetId: aid } })
  }
  let failSave = false
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    const fulfill = (data: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
    if (path.endsWith(`/assets/${aid}/content`)) {
      expect(route.request().headers()['authorization']?.startsWith('Bearer ')).toBeTruthy()
      return route.fulfill({
        contentType: 'image/png',
        body: Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=',
          'base64',
        ),
      })
    }
    if (path.endsWith('/assets')) return fulfill(assets)
    if (path === '/api/workspaces')
      return fulfill([{ id: wid, name: 'Regional resilience', role: 'owner' }])
    if (
      path === '/api/environments' ||
      path.endsWith('/assets') ||
      path.endsWith('/connections') ||
      path.endsWith('/comments') ||
      path.endsWith('/revisions')
    )
      return fulfill([])
    if (path.endsWith('/planning') && method === 'GET') return fulfill(requests)
    if (path.endsWith('/planning') && method === 'POST') {
      const input = route.request().postDataJSON()
      const proposed = structuredClone(scenario.content)
      proposed.title = 'Coordinated flood response'
      requests = [
        ...requests,
        {
          id: input.request_id,
          prompt: input.prompt,
          actor: 'fixture',
          base_version: input.base_version,
          status: 'proposed',
          error: null,
          created_at: '2026-01-01T00:00:00Z',
          proposal: { summary: 'Clarify the coordinated response intent.', content: proposed },
        },
      ]
      return fulfill({ id: input.request_id, status: 'queued' }, 202)
    }
    if (path.endsWith('/apply')) {
      const proposal = requests.find((p) => path.includes(p.id))!
      if (proposal.base_version !== scenario.version)
        return fulfill({ detail: 'Proposal is stale; request a new proposal' }, 409)
      scenario = { ...scenario, version: scenario.version + 1, content: proposal.proposal!.content }
      proposal.status = 'applied'
      return fulfill({ scenario })
    }
    if (path === `/api/workspaces/${wid}/scenarios/${sid}`) {
      if (method === 'GET') return fulfill(scenario)
      if (failSave) return fulfill({ detail: 'Storage unavailable; the draft was not saved.' }, 503)
      if (route.request().headers()['if-match'] !== `"${scenario.version}"`)
        return fulfill({ detail: { message: 'A newer draft exists', current: scenario } }, 409)
      scenario = {
        ...scenario,
        version: scenario.version + 1,
        content: route.request().postDataJSON(),
      }
      return fulfill(scenario)
    }
    throw new Error(`Unhandled test fixture route: ${method} ${path}`)
  })
  return {
    changeServer() {
      scenario = {
        ...scenario,
        version: scenario.version + 1,
        content: { ...scenario.content, title: 'Another editor changed this' },
      }
    },
    failSaves() {
      failSave = true
    },
  }
}

test('uses the concept brand mark in both themes', async ({ page }) => {
  await fixture(page)
  await page.goto('/test.html')
  const mark = page.locator('.brand-mark')
  await expect(mark).toBeVisible()
  await expect(mark).toHaveAttribute('aria-hidden', 'true')
  const bars = mark.locator('i')
  await expect(bars).toHaveCount(3)
  await expect(bars.nth(0)).toHaveCSS('height', '18px')
  await expect(bars.nth(1)).toHaveCSS('height', '29px')
  await expect(bars.nth(2)).toHaveCSS('height', '18px')
  const lightColor = await bars.first().evaluate((bar) => getComputedStyle(bar).backgroundColor)
  await page.getByRole('button', { name: 'Use dark theme' }).click()
  await expect(bars.first()).not.toHaveCSS('background-color', lightColor)
  await expect(mark).toBeVisible()
})

test('preserves input across focused editors and reloads saved content', async ({ page }) => {
  await fixture(page)
  await page.goto('/test.html')
  await page.getByLabel('Scenario title').fill('Updated regional scenario')
  await page.getByLabel('Planning message').fill('Keep this unfinished question')
  await page.getByRole('button', { name: 'Flow', exact: true }).click()
  await page.getByText('Occupancy above 85%', { exact: true }).click()
  await page.getByLabel('Label', { exact: true }).fill('Occupancy above 80%')
  await page.getByText('Generated Mermaid source').click()
  await expect(page.getByTestId('mermaid-source')).toContainText('Occupancy above 80#37;')
  await page.getByRole('button', { name: 'Plan', exact: true }).click()
  await expect(page.getByLabel('Scenario title')).toHaveValue('Updated regional scenario')
  await expect(page.getByLabel('Planning message')).toHaveValue('Keep this unfinished question')
  await page.getByRole('button', { name: 'Save draft', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Draft saved.')
  await page.getByLabel('Planning message').fill('')
  await page.reload()
  await expect(page.getByLabel('Scenario title')).toHaveValue('Updated regional scenario')
  await expect(page.getByText('Occupancy above 80%', { exact: true })).toHaveCount(2)
})

test('keeps local draft on conflicts and failed saves', async ({ page }) => {
  const server = await fixture(page)
  await page.goto('/test.html')
  await page.getByLabel('Scenario title').fill('My unsaved work')
  server.changeServer()
  await page.getByRole('button', { name: 'Save draft', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Export my draft' })).toBeVisible()
  await expect(page.getByLabel('Scenario title')).toHaveValue('My unsaved work')
  server.failSaves()
  await page.getByRole('button', { name: 'Save draft', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Storage unavailable')
  await expect(page.getByLabel('Scenario title')).toHaveValue('My unsaved work')
})

test('proposal application is explicit and stale updates stay blocked', async ({ page }) => {
  const server = await fixture(page)
  await page.goto('/test.html')
  await page.getByLabel('Planning message').fill('Clarify our response intent')
  await page.getByRole('button', { name: 'Request proposal' }).click()
  await page.getByRole('button', { name: 'Review proposed changes' }).click()
  await expect(
    page.getByRole('heading', { name: 'Regional flood response', exact: true }),
  ).toBeVisible()
  server.changeServer()
  await page.getByRole('button', { name: 'Apply reviewed proposal' }).click()
  await expect(page.getByRole('alert')).toContainText('stale')
  await expect(
    page.getByRole('heading', { name: 'Regional flood response', exact: true }),
  ).toBeVisible()
})

test('accepted proposal updates the real studio state', async ({ page }) => {
  await fixture(page)
  await page.goto('/test.html')
  await page.getByLabel('Planning message').fill('Clarify our response intent')
  await page.getByRole('button', { name: 'Request proposal' }).click()
  await page.getByRole('button', { name: 'Review proposed changes' }).click()
  await page.getByRole('button', { name: 'Apply reviewed proposal' }).click()
  await expect(page.getByLabel('Scenario title')).toHaveValue('Coordinated flood response')
  await expect(page.getByText('Proposal applied and saved as a new draft version.')).toBeVisible()
})

test('leaving unsaved work requires an explicit decision', async ({ page }) => {
  await fixture(page)
  await page.goto('/test.html')
  await page.getByLabel('Scenario title').fill('Do not lose this')
  await page.getByRole('link', { name: 'Regional resilience' }).click()
  await expect(page.getByRole('alertdialog')).toBeVisible()
  await page.getByRole('button', { name: 'Keep editing' }).click()
  await expect(page.getByLabel('Scenario title')).toHaveValue('Do not lose this')
})

test('embeds an authorized immutable image in the document', async ({ page }) => {
  await fixture(page, true)
  await page.goto('/test.html')
  await page.getByRole('button', { name: 'Situation map', exact: true }).scrollIntoViewIfNeeded()
  const image = page.getByRole('img', { name: 'Situation map' })
  await expect(image).toBeVisible()
  await expect
    .poll(() => image.evaluate((element) => (element as HTMLImageElement).naturalWidth))
    .toBe(1)
})

test('Markdown import is keyboard accessible and rejects raw HTML', async ({ page }) => {
  await fixture(page)
  await page.goto('/test.html')
  const picker = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: 'Import Markdown' }).press('Enter')
  await (
    await picker
  ).setFiles({
    name: 'unsafe.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('<script>alert("not allowed")</script>'),
  })
  await expect(page.getByRole('alert')).toContainText('Raw HTML')
})

for (const width of [1440, 900, 390]) {
  for (const dark of [false, true]) {
    test(`responsive studio ${width}px ${dark ? 'dark' : 'light'}`, async ({ page }) => {
      const errors: string[] = []
      page.on('pageerror', (error) => errors.push(error.message))
      await fixture(page)
      await page.setViewportSize({ width, height: 1000 })
      await page.goto('/test.html')
      await expect(page.getByLabel('Scenario title')).toBeVisible()
      if (dark) await page.getByRole('button', { name: 'Use dark theme' }).click()
      for (const pane of ['Plan', 'Flow', 'Assets', 'Review & history']) {
        await page.getByRole('button', { name: pane, exact: true }).click()
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        ).toBeTruthy()
      }
      await page.getByRole('button', { name: 'Plan', exact: true }).click()
      await page.screenshot({
        path: `test-results/studio-${width}-${dark ? 'dark' : 'light'}.png`,
        fullPage: true,
      })
      expect(errors).toEqual([])
    })
  }
}
