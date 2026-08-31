/**
 * End-to-end verification of the chat flow against the real backend,
 * driving the actual UI in a headless browser (uses the system Chrome).
 *
 *   node e2e/verify-flow.mjs
 *
 * Exercises: sign-up → workspace → characters/models from the API → create a
 * conversation → open it → send a message → streamed AI reply → persisted
 * history after reload → second conversation → switching between them.
 * Fails loudly on console errors or failed network requests.
 */

import { chromium } from 'playwright'

// Usable against the dev server (5173) or any preview instance:
//   APP_URL=http://localhost:5932 node e2e/verify-flow.mjs
const APP_URL = process.env.APP_URL ?? 'http://localhost:5173'

const results = []
function check(name, ok, extra = '') {
  results.push({ name, ok, extra })
  console.log(`${ok ? '  ✓' : '  ✗ FAIL'} ${name}${extra ? ` — ${extra}` : ''}`)
}

/** Auto-waiting visibility check for content loaded asynchronously. */
async function checkVisible(name, locator, timeout = 15000) {
  try {
    await locator.waitFor({ state: 'visible', timeout })
    check(name, true)
  } catch {
    check(name, false)
  }
}

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1366, height: 860 } })

const consoleErrors = []
const failedRequests = []
page.on('console', (msg) => {
  if (msg.type() === 'error') consoleErrors.push(msg.text())
})
page.on('pageerror', (error) => consoleErrors.push(`pageerror: ${error.message}`))
page.on('requestfailed', (request) => {
  failedRequests.push(`${request.method()} ${request.url()} — ${request.failure()?.errorText}`)
})
page.on('response', (response) => {
  // The app calls the backend same-origin via the Vite /api proxy in both dev
  // and preview, so failed API calls show up as /api/* requests.
  if (response.url().includes('/api/') && response.status() >= 400) {
    failedRequests.push(`${response.request().method()} ${response.url()} → ${response.status()}`)
  }
})

const username = `e2e_${Date.now().toString(36)}`
const password = 'e2e-password-123'

try {
  console.log('\n1. App starts → auth screen')
  await page.goto(APP_URL, { waitUntil: 'networkidle' })
  check('page renders the sign-in screen', await page.getByRole('tab', { name: 'Sign in' }).isVisible())

  console.log('\n2. Create account')
  await page.getByRole('tab', { name: 'Create account' }).click()
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Display name', { exact: false }).fill('E2E Tester')
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.waitForSelector('aside', { timeout: 15000 })
  check('workspace appears after sign-up', true)

  console.log('\n3. Conversations sidebar (empty state)')
  await checkVisible('empty state shown', page.getByText('No conversations yet'))

  console.log('\n4. New chat modal loads characters + models from the API')
  await page.getByRole('button', { name: 'New chat' }).click()
  await page.getByText('Who do you want to talk to?').waitFor({ timeout: 10000 })
  for (const name of ['Maya', 'Mia', 'Ema']) {
    await checkVisible(`character "${name}" listed`, page.getByRole('radio', { name, exact: true }))
  }
  for (const model of ['DeepSeek v4 Pro', 'DeepSeek V4 Flash']) {
    await checkVisible(`model "${model}" listed`, page.getByRole('radio', { name: new RegExp(model) }))
  }

  console.log('\n5. Create conversation (Maya)')
  await page.getByRole('radio', { name: 'Maya', exact: true }).click()
  await page.getByRole('button', { name: 'Start chatting' }).click()
  await page.getByText('Say hello to Maya').waitFor({ timeout: 10000 })
  check('new conversation opens with empty state', true)
  check('conversation appears in sidebar', await page.getByTitle('Maya').first().isVisible())

  console.log('\n6. Send a message → streamed reply')
  const composer = page.getByLabel('Message Maya')
  await composer.fill('Hi Maya! Tell me one fun fact about space.')
  await composer.press('Enter')
  await page.getByText('Hi Maya! Tell me one fun fact about space.', { exact: false }).first().waitFor({ timeout: 10000 })
  check('user message displayed', true)

  await page.getByText(/Maya is typing|is replying/).first().waitFor({ timeout: 5000 }).catch(() => {})
  await page.waitForFunction(
    () => {
      const bubbles = [...document.querySelectorAll('p')].map((p) => p.textContent ?? '')
      return bubbles.some((t) => /fun fact/i.test(t) && t.length > 40)
    },
    { timeout: 60000 },
  )
  check('assistant reply arrived', true)

  console.log('\n7. Reply persists across a reload')
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByText('Hi Maya! Tell me one fun fact about space.', { exact: false }).first().waitFor({ timeout: 15000 })
  check('active chat restored after reload', true)
  check('conversation still in sidebar after reload', await page.getByTitle('Maya').first().isVisible())

  console.log('\n8. Create a second conversation and switch')
  await page.getByRole('button', { name: 'New chat' }).click()
  await page.getByRole('radio', { name: 'Ema', exact: true }).click()
  await page.getByRole('button', { name: 'Start chatting' }).click()
  await page.getByText('Say hello to Ema').waitFor({ timeout: 10000 })
  check('second conversation opened', true)
  const sidebarItems = await page.locator('[role="listitem"]').count()
  check('two conversations in sidebar', sidebarItems >= 2, `${sidebarItems} items`)
  await page.getByTitle('Maya').first().click()
  await page.getByText('Hi Maya! Tell me one fun fact about space.', { exact: false }).first().waitFor({ timeout: 10000 })
  check('switching back loads Maya history', true)

  console.log('\n9. Console + network hygiene')
  check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))
  check('no failed API requests', failedRequests.length === 0, failedRequests.slice(0, 3).join(' | '))

  // Cleanup: delete both conversations via the UI delete buttons.
  console.log('\n10. Cleanup (delete test conversations)')
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
  check('conversations cleaned up', (await items.count()) === 0)
} catch (error) {
  check('test flow completed without exceptions', false, String(error))
  await page.screenshot({ path: 'e2e/failure.png', fullPage: true })
  console.log('screenshot saved to e2e/failure.png')
} finally {
  const failed = results.filter((r) => !r.ok)
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
  await browser.close()
  process.exit(failed.length > 0 ? 1 : 0)
}
