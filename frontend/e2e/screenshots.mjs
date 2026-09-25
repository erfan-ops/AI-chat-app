/**
 * Visual verification: captures the UI in light / dark / mobile and checks
 * the narrow-screen drawer behavior. Screenshots land in e2e/shots/.
 *
 *   node e2e/screenshots.mjs
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

mkdirSync('e2e/shots', { recursive: true })

const APP_URL = process.env.APP_URL ?? 'http://localhost:5173'
const browser = await chromium.launch({ channel: 'chrome', headless: true })

const username = `shot_${Date.now().toString(36)}`
const password = 'shot-password-123'

// --- Desktop, light ---
const page = await browser.newPage({ viewport: { width: 1366, height: 860 } })
await page.goto(APP_URL, { waitUntil: 'domcontentloaded' })
await page.waitForLoadState('load')
await page.getByRole('tab', { name: 'Create account' }).click()
await page.getByLabel('Username').fill(username)
await page.getByLabel('Password').fill(password)
await page.getByRole('button', { name: 'Create account' }).click()
await page.waitForSelector('aside', { timeout: 15000 })
await page.screenshot({ path: 'e2e/shots/01-workspace-empty.png' })

await page.getByRole('button', { name: 'New chat' }).click()
await page.getByText('Who do you want to talk to?').waitFor({ timeout: 10000 })
await page.screenshot({ path: 'e2e/shots/02-new-chat-modal.png' })
await page.getByRole('radio', { name: 'Maya', exact: true }).click()
await page.getByRole('button', { name: 'Start chatting' }).click()
await page.getByText('Say hello to Maya').waitFor({ timeout: 10000 })
await page.screenshot({ path: 'e2e/shots/03-empty-chat.png' })

await page.getByLabel('Message Maya').fill('Hey Maya! What should we do this weekend? 🌤️')
await page.getByLabel('Message Maya').press('Enter')
await page.waitForFunction(
  () => [...document.querySelectorAll('p')].some((p) => /weekend/i.test(p.textContent ?? '') && (p.textContent ?? '').length > 30),
  { timeout: 60000 },
)
await page.waitForTimeout(800)
await page.screenshot({ path: 'e2e/shots/04-chat-with-reply.png' })

// Second exchange to show grouping + day separator behavior.
await page.getByLabel('Message Maya').fill('Sounds fun!')
await page.getByLabel('Message Maya').press('Enter')
// Wait for a second assistant-sized reply (content-agnostic).
await page.waitForFunction(
  () => [...document.querySelectorAll('p')].filter((p) => (p.textContent ?? '').length > 30).length >= 2,
  { timeout: 60000 },
)
await page.waitForTimeout(800)
await page.screenshot({ path: 'e2e/shots/05-chat-multi-message.png' })

// --- Desktop, dark ---
const dark = await browser.newPage({
  viewport: { width: 1366, height: 860 },
  colorScheme: 'dark',
})
await dark.goto(APP_URL, { waitUntil: 'domcontentloaded' })
await dark.waitForLoadState('load')
await dark.getByRole('tab', { name: 'Sign in' }).click()
await dark.getByLabel('Username').fill(username)
await dark.getByLabel('Password').fill(password)
await dark.getByRole('button', { name: 'Sign in' }).click()
await dark.waitForSelector('aside', { timeout: 15000 })
await dark.getByTitle('Maya').first().click()
await dark.getByText('Hey Maya! What should we do this weekend?', { exact: false }).first().waitFor({ timeout: 15000 })
await dark.screenshot({ path: 'e2e/shots/06-dark-chat.png' })

// --- Mobile: drawer behavior ---
const mobile = await browser.newPage({
  viewport: { width: 390, height: 844 },
  isMobile: true,
  hasTouch: true,
})
await mobile.goto(APP_URL, { waitUntil: 'domcontentloaded' })
await mobile.waitForLoadState('load')
await mobile.getByRole('tab', { name: 'Sign in' }).click()
await mobile.getByLabel('Username').fill(username)
await mobile.getByLabel('Password').fill(password)
await mobile.getByRole('button', { name: 'Sign in' }).click()
await mobile.waitForSelector('aside', { timeout: 15000 })
await mobile.screenshot({ path: 'e2e/shots/07-mobile-sidebar.png' })

await mobile.getByTitle('Maya').first().click()
await mobile.getByText('Hey Maya! What should we do this weekend?', { exact: false }).first().waitFor({ timeout: 15000 })
const backVisible = await mobile.getByRole('button', { name: 'Back to conversations' }).isVisible()
console.log(`mobile back button visible: ${backVisible ? 'OK' : 'FAIL'}`)
await mobile.screenshot({ path: 'e2e/shots/08-mobile-chat.png' })

await mobile.getByRole('button', { name: 'Back to conversations' }).click()
const sidebarBack = await mobile.getByRole('button', { name: 'New chat' }).isVisible()
console.log(`mobile back → sidebar visible: ${sidebarBack ? 'OK' : 'FAIL'}`)
await mobile.screenshot({ path: 'e2e/shots/09-mobile-back-to-sidebar.png' })

await browser.close()
console.log('done — screenshots in e2e/shots/')
