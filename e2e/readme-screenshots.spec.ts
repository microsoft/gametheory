import { expect, test as base, type Locator, type Page, type TestInfo } from '@playwright/test'
import { ids, riverwatchFixture } from './riverwatch.fixture'

/*
 * README screenshot scenario: the synthetic Riverwatch flood exercise in the explicit test-mode
 * fixture harness. The regular e2e run checks this walkthrough and keeps its images with the
 * test output. `npm run screenshots` sets `readmeImages`, so each image is compared with the
 * tracked README image and rewritten only when it visibly changed.
 */
type ScreenshotOptions = { readmeImages: boolean }
const test = base.extend<ScreenshotOptions & { riverwatch: { unhandled: string[] } }>({
  readmeImages: [false, { option: true }],
  riverwatch: [
    async ({ page }, use) => {
      const errors: string[] = []
      page.on('pageerror', (error) => errors.push(error.message))
      const fixture = await riverwatchFixture(page)
      await use(fixture)
      expect(fixture.unhandled).toEqual([])
      expect(errors).toEqual([])
    },
    { auto: true },
  ],
})
test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US' })

/** Part of a long page: from the top of one element to the bottom of another, optionally cropped to a panel's sides. */
type Region = { top: Locator; bottom?: Locator; sides?: Locator }

async function clip(page: Page, region: Region) {
  const box = (locator: Locator) =>
    locator.evaluate((element) => {
      const rect = element.getBoundingClientRect()
      return {
        left: rect.left + scrollX,
        right: rect.right + scrollX,
        top: rect.top + scrollY,
        bottom: rect.bottom + scrollY,
      }
    })
  const margin = 12
  const viewport = page.viewportSize()!
  const top = Math.max(0, (await box(region.top)).top - margin)
  const bottom = region.bottom ? (await box(region.bottom)).bottom + margin : top + viewport.height
  const sides = region.sides && (await box(region.sides))
  const left = sides ? Math.max(0, sides.left - margin) : 0
  const right = sides ? Math.min(viewport.width, sides.right + margin) : viewport.width
  return { x: left, y: top, width: right - left, height: bottom - top }
}

async function capture(
  page: Page,
  info: TestInfo,
  readmeImages: boolean,
  name: string,
  region?: Region,
) {
  const file = `${name}.png`
  const options = {
    animations: 'disabled' as const,
    ...(region && { fullPage: true, clip: await clip(page, region) }),
  }
  if (readmeImages) await expect(page).toHaveScreenshot(file, options)
  else await page.screenshot({ ...options, path: info.outputPath(file) })
}

const workspace = `/w/${ids.workspace}`

test('studio plan: document-first authoring with a reviewable proposal', async ({
  page,
  readmeImages,
}, info) => {
  await page.goto(`${workspace}/s/${ids.scenario}`)
  await expect(page.getByLabel('Scenario title')).toHaveValue(
    'Riverwatch: shelter capacity and escalation',
  )
  await expect(page.getByText('Saved draft', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Situation', exact: true })).toBeVisible()
  // Show the latest planning turn, as the owner returning to review it would see it.
  await page
    .locator('.conversation-turn')
    .last()
    .evaluate((turn) => turn.scrollIntoView({ block: 'start' }))
  await expect(page.getByRole('button', { name: 'Review proposed changes' })).toBeInViewport()
  await capture(page, info, readmeImages, 'studio-plan')
})

test('studio flow: branching scenario flow bound to inventory', async ({
  page,
  readmeImages,
}, info) => {
  await page.goto(`${workspace}/s/${ids.scenario}`)
  await page.getByRole('button', { name: 'Flow', exact: true }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(6)
  // Wait until the editor has fitted every step into the canvas.
  await expect
    .poll(() =>
      page.getByLabel('Scenario flow canvas').evaluate((canvas) => {
        const frame = canvas.getBoundingClientRect()
        return [...canvas.querySelectorAll('.react-flow__node')].every((node) => {
          const box = node.getBoundingClientRect()
          return box.top >= frame.top && box.bottom <= frame.bottom
        })
      }),
    )
    .toBe(true)
  await capture(page, info, readmeImages, 'studio-flow')
})

test('preparation board: steps bound to exact registered operations', async ({
  page,
  readmeImages,
}, info) => {
  await page.goto(`${workspace}/boards/${ids.board}`)
  await expect(page.getByText('Preparation approved — not authorized to execute')).toBeVisible()
  const step = page.getByRole('region', { name: 'Preparation step 2' })
  await expect(step.getByRole('heading', { name: '2. Inject Aster Reach occupancy' })).toBeVisible()
  const version = step.getByRole('group', { name: 'expected_version · string' })
  await expect(version.getByText('Read seeded occupancy · record_version')).toBeAttached()
  await capture(page, info, readmeImages, 'preparation-board', {
    top: step,
    bottom: version,
    sides: page.getByRole('region', { name: 'Preparation draft' }),
  })
})

test('exercise runs: run checks suggested from plain words', async ({
  page,
  readmeImages,
}, info) => {
  await page.goto(`${workspace}/boards/${ids.board}`)
  await page.getByRole('button', { name: 'Exercise runs' }).click()
  const assistant = page.getByRole('region', { name: 'Describe what to check' })
  await expect(assistant.getByText('Suggestions ready')).toBeVisible()
  await expect(assistant.getByText('Valid', { exact: true })).toHaveCount(5)
  await capture(page, info, readmeImages, 'run-checks', {
    top: page.getByRole('navigation', { name: 'Board sections' }),
  })
})

test('exercise run: objectives judged from recorded evidence', async ({
  page,
  readmeImages,
}, info) => {
  await page.goto(`${workspace}/runs/${ids.run}`)
  await expect(page.getByRole('status').filter({ hasText: 'completed' })).toBeVisible()
  const findings = page.getByRole('heading', { name: 'Objective assessment' }).locator('..')
  await expect(findings.getByText('met', { exact: true })).toHaveCount(2)
  await expect(findings.getByText('unmet', { exact: true })).toBeVisible()
  await capture(page, info, readmeImages, 'run-results', {
    top: findings,
    bottom: findings,
    sides: findings,
  })
})

test('settings: environment execution policy in the dark theme', async ({
  page,
  readmeImages,
}, info) => {
  await page.addInitScript(() => localStorage.setItem('gt-theme', 'dark'))
  await page.goto('/settings')
  await page.getByRole('button', { name: /Flood lab/ }).click()
  await expect(page.getByLabel('Allow configured execution in this environment')).toBeChecked()
  await expect(page.getByRole('button', { name: 'Use light theme' })).toBeVisible()
  await capture(page, info, readmeImages, 'settings-dark')
})
