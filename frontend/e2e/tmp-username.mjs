/** Throwaway: verify the username change in the real UI. Deleted after the run. */
import { chromium } from 'playwright'

const APP_URL = process.env.APP_URL ?? 'http://localhost:5173'
const results = []
function check(name, ok, extra = '') {
  results.push({ name, ok, extra })
  console.log(`${ok ? '  ✓' : '  ✗ FAIL'} ${name}${extra ? ` — ${extra}` : ''}`)
}

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const stamp = Date.now().toString(36)
const aliceName = `alice_${stamp}`
const bobName = `bob_${stamp}`
const newName = `alice_new_${stamp}`
const password = 'e2e-password-123'

async function signUp(page, username) {
  await page.goto(APP_URL, { waitUntil: 'networkidle' })
  await page.getByRole('tab', { name: 'Create account' }).click()
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.waitForSelector('aside', { timeout: 15000 })
}

const page = await browser.newPage({ viewport: { width: 1366, height: 900 } })
const consoleErrors = []
page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))
page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`))

try {
  // A second account to collide with.
  const other = await browser.newPage({ viewport: { width: 1366, height: 900 } })
  await signUp(other, bobName)
  check('second account created', true)
  await other.close()

  console.log('\n1. Sign up with no display name → the sidebar shows the username')
  await signUp(page, aliceName)
  await page.getByRole('button', { name: 'Settings' }).click()
  const usernameField = page.getByLabel('Username')
  await usernameField.waitFor({ state: 'visible', timeout: 10000 })
  check('settings shows the username field', (await usernameField.inputValue()) === aliceName)

  console.log('\n2. Taking an existing username is refused')
  await usernameField.fill(bobName)
  await page.getByRole('button', { name: 'Save profile' }).click()
  const conflict = page.getByText('Username is already taken')
  await conflict.waitFor({ state: 'visible', timeout: 10000 }).catch(() => {})
  check('duplicate username shows the API error', await conflict.isVisible())
  check('still signed in (not bounced to auth)', await page.getByRole('button', { name: 'Settings' }).isVisible())
  const after = await page.evaluate(() => localStorage.getItem('ai-chat.session'))
  check('session survived the 409', Boolean(after), after ? '' : 'session cleared')

  console.log('\n3. Invalid usernames disable the save button')
  await usernameField.fill('ab')
  check(
    'too short → save disabled',
    await page.getByRole('button', { name: 'Save profile' }).isDisabled(),
  )
  await usernameField.fill('has space')
  check(
    'bad character → save disabled',
    await page.getByRole('button', { name: 'Save profile' }).isDisabled(),
  )

  console.log('\n4. A free username is accepted and reflected without a reload')
  await usernameField.fill(newName)
  await page.getByRole('button', { name: 'Save profile' }).click()
  const saved = page.getByText('Settings saved')
  await saved.waitFor({ state: 'visible', timeout: 10000 }).catch(() => {})
  check('save confirmation shown', await saved.isVisible())
  const sidebar = page.locator('aside')
  await sidebar.getByText(newName).waitFor({ state: 'visible', timeout: 5000 }).catch(() => {})
  check('sidebar shows the new username', await sidebar.getByText(newName).isVisible())

  console.log('\n5. The rename survives a reload')
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('aside', { timeout: 15000 })
  check('still signed in after reload', await page.locator('aside').getByText(newName).isVisible())
  await page.screenshot({ path: 'e2e/shots/tmp-username-settings.png' })
  await page.getByRole('button', { name: 'Settings' }).click()
  await page.getByLabel('Username').waitFor({ state: 'visible', timeout: 10000 })
  check('settings shows the renamed value', (await page.getByLabel('Username').inputValue()) === newName)
  await page.keyboard.press('Escape')

  console.log('\n6. Sign in with the new username')
  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.getByRole('tab', { name: 'Sign in' }).click()
  await page.getByLabel('Username').fill(newName)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.waitForSelector('aside', { timeout: 15000 })
  check('login with the new username works', await page.locator('aside').getByText(newName).isVisible())

  check('no console errors', consoleErrors.length === 0, consoleErrors.join(' | '))
} catch (error) {
  check('script completed', false, String(error))
} finally {
  await browser.close()
}

const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
process.exit(failed.length ? 1 : 0)
