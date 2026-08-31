/**
 * Layout + visual-state verification via computed styles and geometry
 * (the Read tool can't render screenshots in this environment).
 *
 *   node e2e/layout-check.mjs
 */

import { chromium } from 'playwright'

const APP_URL = process.env.APP_URL ?? 'http://localhost:5173'
const results = []
function check(name, ok, extra = '') {
  results.push({ name, ok })
  console.log(`${ok ? '  ✓' : '  ✗ FAIL'} ${name}${extra ? ` — ${extra}` : ''}`)
}

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const username = `lay_${Date.now().toString(36)}`
const password = 'lay-password-123'

const page = await browser.newPage({ viewport: { width: 1366, height: 860 } })
await page.goto(APP_URL, { waitUntil: 'networkidle' })
await page.getByRole('tab', { name: 'Create account' }).click()
await page.getByLabel('Username').fill(username)
await page.getByLabel('Password').fill(password)
await page.getByRole('button', { name: 'Create account' }).click()
await page.waitForSelector('aside', { timeout: 15000 })
await page.getByRole('button', { name: 'New chat' }).click()
await page.getByRole('radio', { name: 'Maya', exact: true }).click()
await page.getByRole('button', { name: 'Start chatting' }).click()
await page.getByText('Say hello to Maya').waitFor({ timeout: 10000 })

// Desktop geometry
const asideBox = await page.locator('aside').boundingBox()
check('sidebar is 320px wide', asideBox && Math.round(asideBox.width) === 320, asideBox ? `${Math.round(asideBox.width)}px` : 'no box')
check('no horizontal overflow (desktop)', await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))

// Send a message; capture state DURING the stream and AFTER it.
const composer = page.getByLabel('Message Maya')
await composer.fill('A quick hello!')
await composer.press('Enter')
await page.getByText('A quick hello!', { exact: false }).first().waitFor({ timeout: 10000 })

// During the stream: composer disabled + typing indicator present.
try {
  await page.getByText('Maya is typing…').first().waitFor({ timeout: 8000 })
  check('typing indicator shows while streaming', true)
} catch {
  // The reply may have finished too fast to observe — verify after-the-fact instead.
  check('typing indicator shows while streaming', false, 'reply completed before observation')
}
const disabledDuring = await composer.isDisabled()
check('composer disabled while replying', disabledDuring)

// Wait for the assistant reply to finish.
await page.waitForFunction(
  () => [...document.querySelectorAll('p')].some((p) => (p.textContent ?? '').length > 25),
  { timeout: 60000 },
)
await page.waitForFunction(() => !document.body.textContent?.includes('is typing'), { timeout: 15000 })
const disabledAfter = await composer.isDisabled()
check('composer re-enabled after reply', !disabledAfter)

// Bubble alignment + colors (light theme)
const userBubble = page.locator('div[class*="bubbleMine"]', { hasText: 'A quick hello!' })
const assistantBubble = page.locator('div[class*="bubbleTheirs"]').first()
const userBg = await userBubble.evaluate((el) => getComputedStyle(el).backgroundColor)
const userColor = await userBubble.evaluate((el) => getComputedStyle(el).color)
check('user bubble uses the accent background', userBg === 'rgb(79, 70, 229)', userBg)
check('user bubble text is white', userColor === 'rgb(255, 255, 255)', userColor)

const userBox = await userBubble.boundingBox()
const assistantBox = await assistantBubble.boundingBox()

// Bubble sizing: max 72% of the content column; the content column itself is
// centered (max-width 860px) inside the pane, so alignment is measured
// against the column edges, not the pane edges.
const column = await page.evaluate(() => {
  const el = [...document.querySelectorAll('div')].find((d) =>
    d.className.toString().includes('content'),
  )
  if (!el) return null
  const rect = el.getBoundingClientRect()
  return { left: rect.left, right: rect.right, width: rect.width }
})
const userRightGap = column.right - (userBox.x + userBox.width)
const assistantLeft = assistantBox.x - column.left
check('user bubble right-aligned (near column edge)', userRightGap < 25, `gap ${Math.round(userRightGap)}px`)
check('assistant bubble left-aligned (after avatar)', assistantLeft < 90, `left ${Math.round(assistantLeft)}px`)
check(
  'bubbles capped at 72% width',
  column !== null && assistantBox.width <= column.width * 0.72 + 1,
  `${Math.round(assistantBox.width)}px of ${Math.round(column?.width ?? 0)}px`,
)

// Timestamps + day separator rendered (times may carry a locale suffix like "PM").
const hasTime = await page.evaluate(() =>
  [...document.querySelectorAll('span')].some((s) => /^\d{1,2}:\d{2}/.test(s.textContent?.trim() ?? '')),
)
check('message timestamps rendered', hasTime)
const hasDay = await page.locator('span', { hasText: /^Today$/ }).count()
check('day separator rendered', hasDay > 0)

// Header shows the character name and model (scoped to the chat header).
check('chat header shows character name', await page.locator('header h1', { hasText: 'Maya' }).isVisible())
check('chat header shows model name', await page.locator('header p', { hasText: 'DeepSeek v4 Pro' }).isVisible())

// Focus visibility: keyboard focus on the textarea highlights the composer
// container with a focus ring.
await composer.focus()
const focusShadow = await composer
  .locator('..')
  .evaluate((el) => getComputedStyle(el).boxShadow)
check('visible focus style on composer', focusShadow !== 'none', focusShadow.slice(0, 60))

// --- Dark theme ---
const dark = await browser.newPage({ viewport: { width: 1366, height: 860 }, colorScheme: 'dark' })
await dark.goto(APP_URL, { waitUntil: 'networkidle' })
await dark.getByRole('tab', { name: 'Sign in' }).click()
await dark.getByLabel('Username').fill(username)
await dark.getByLabel('Password').fill(password)
await dark.getByRole('button', { name: 'Sign in' }).click()
await dark.waitForSelector('aside', { timeout: 15000 })
await dark.getByTitle('Maya').first().click()
await dark.getByText('A quick hello!', { exact: false }).first().waitFor({ timeout: 15000 })
const darkBg = await dark.evaluate(() => getComputedStyle(document.body).backgroundColor)
check('dark theme applied (body uses dark token)', darkBg === 'rgb(12, 15, 20)', darkBg)

// --- Mobile: no horizontal overflow in both states ---
const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
await mobile.goto(APP_URL, { waitUntil: 'networkidle' })
await mobile.getByRole('tab', { name: 'Sign in' }).click()
await mobile.getByLabel('Username').fill(username)
await mobile.getByLabel('Password').fill(password)
await mobile.getByRole('button', { name: 'Sign in' }).click()
await mobile.waitForSelector('aside', { timeout: 15000 })
check('no horizontal overflow (mobile sidebar)', await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
await mobile.getByTitle('Maya').first().click()
await mobile.getByText('A quick hello!', { exact: false }).first().waitFor({ timeout: 15000 })
check('no horizontal overflow (mobile chat)', await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1))
const bubbleBox = await mobile.locator('div[class*="bubbleMine"]', { hasText: 'A quick hello!' }).boundingBox()
check('mobile bubble stays inside viewport', bubbleBox.x >= 0 && bubbleBox.x + bubbleBox.width <= 390)

// Cleanup: delete the conversation via the UI.
await page.getByRole('button', { name: 'New chat' }).waitFor()
const items = page.locator('[role="listitem"]')
const count = await items.count()
for (let i = 0; i < count; i++) {
  const item = items.first()
  await item.hover()
  const deleteBtn = item.getByRole('button', { name: /^Delete / })
  if (await deleteBtn.isVisible()) {
    await deleteBtn.click()
    await page.getByRole('button', { name: 'Delete', exact: true }).click()
    await page.waitForTimeout(400)
  }
}

const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
await browser.close()
process.exit(failed.length > 0 ? 1 : 0)
