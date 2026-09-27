/**
 * Responsive audit: every important surface, measured across the viewport sizes the
 * application has to work at.
 *
 *   node e2e/responsive-check.mjs                     # against the dev server
 *   APP_URL=http://localhost:5931 node e2e/responsive-check.mjs
 *
 * It looks for the failures that are invisible in a screenshot: page-level horizontal
 * overflow, elements crossing the viewport edge, containers that grew a horizontal
 * scrollbar, controls too small to tap, and affordances that only exist on hover.
 * Each is reported with the element that caused it, so a fix has somewhere to start.
 *
 * Needs a running backend (it registers a throwaway account and creates one
 * conversation, which it deletes again on the way out).
 */

import { createHmac } from 'node:crypto'
import { chromium } from 'playwright'

const APP_URL = process.env.APP_URL ?? 'http://localhost:5173'

// -- A minimal authenticator, so the code steps can be reached for real -----------------
// The second-factor and recovery screens only exist once a code is in flight, and
// sending real SMS or email from an audit is not an option. An enrolled authenticator
// produces codes locally, and needs no provider at all.

function base32Decode(secret) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const char of secret.replace(/=+$/, '').toUpperCase()) {
    const index = alphabet.indexOf(char)
    if (index < 0) continue
    bits += index.toString(2).padStart(5, '0')
  }
  const bytes = []
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2))
  return Buffer.from(bytes)
}

function totp(secret) {
  const counter = Math.floor(Date.now() / 1000 / 30)
  const buffer = Buffer.alloc(8)
  buffer.writeUInt32BE(Math.floor(counter / 2 ** 32), 0)
  buffer.writeUInt32BE(counter % 2 ** 32, 4)
  const digest = createHmac('sha1', base32Decode(secret)).update(buffer).digest()
  const offset = digest[digest.length - 1] & 0x0f
  const code =
    ((digest[offset] & 0x7f) << 24) |
    (digest[offset + 1] << 16) |
    (digest[offset + 2] << 8) |
    digest[offset + 3]
  return String(code % 1_000_000).padStart(6, '0')
}

// The sizes the application is expected to work at: the narrowest phone still in
// use, the common phone sizes, tablets in both orientations, and desktops up to an
// ultrawide. Landscape matters as much as portrait — a short viewport is a different
// problem from a narrow one.
const VIEWPORTS = [
  { name: '320x568 small phone', width: 320, height: 568 },
  { name: '360x640 phone', width: 360, height: 640 },
  { name: '375x667 phone', width: 375, height: 667 },
  { name: '390x844 phone', width: 390, height: 844 },
  { name: '393x852 phone', width: 393, height: 852 },
  { name: '412x915 phone', width: 412, height: 915 },
  { name: '430x932 phone', width: 430, height: 932 },
  { name: '568x320 phone landscape', width: 568, height: 320 },
  { name: '844x390 phone landscape', width: 844, height: 390 },
  { name: '600x960 small tablet', width: 600, height: 960 },
  { name: '768x1024 tablet portrait', width: 768, height: 1024 },
  { name: '800x1280 tablet portrait', width: 800, height: 1280 },
  { name: '820x1180 tablet portrait', width: 820, height: 1180 },
  { name: '1024x768 tablet landscape', width: 1024, height: 768 },
  { name: '1024x1366 tablet landscape', width: 1024, height: 1366 },
  { name: '1280x720 laptop', width: 1280, height: 720 },
  { name: '1366x768 laptop', width: 1366, height: 768 },
  { name: '1440x900 desktop', width: 1440, height: 900 },
  { name: '1536x864 desktop', width: 1536, height: 864 },
  { name: '1920x1080 desktop', width: 1920, height: 1080 },
  { name: '2560x1440 large desktop', width: 2560, height: 1440 },
]

// The surfaces that are worth measuring in detail (dialogs) run at this subset;
// the page-level sweep above covers every size.
const DETAIL_SIZES = new Set(['320x568 small phone', '390x844 phone', '768x1024 tablet portrait', '1024x768 tablet landscape', '1440x900 desktop', '2560x1440 large desktop'])

const results = []
/** Horizontal scroll containers, deduplicated by element: reported, never failed. */
const horizontalScrollers = new Map()
function check(name, ok, extra = '') {
  results.push({ name, ok, extra })
  if (!ok) console.log(`  FAIL ${name}${extra ? ` — ${extra}` : ''}`)
}

/** Measured in the page: what is sticking out, and what grew a scrollbar. */
const probe = () => {
  const viewportWidth = window.innerWidth

  /** An ancestor that clips or scrolls horizontally means it is contained on purpose. */
  const clippedByAncestor = (el) => {
    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
      if (getComputedStyle(parent).overflowX !== 'visible') return true
    }
    return false
  }

  const describe = (el) => {
    const parts = []
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const raw = typeof node.className === 'string' ? node.className.trim().split(/\s+/)[0] : ''
      // CSS-module class names carry a hash suffix; the readable part is enough.
      const name = raw ? raw.replace(/_[A-Za-z0-9-]+$/, '') : ''
      parts.unshift(`${node.tagName.toLowerCase()}${name ? `${name.startsWith('.') ? '' : '.'}${name}` : ''}`)
    }
    return parts.slice(-3).join(' > ')
  }

  const crossing = []
  for (const el of document.querySelectorAll('body *')) {
    const rect = el.getBoundingClientRect()
    if (rect.width < 1 || rect.height < 1) continue
    if (rect.right <= viewportWidth + 1 && rect.left >= -1) continue
    if (clippedByAncestor(el)) continue
    crossing.push({
      el,
      path: describe(el),
      left: Math.round(rect.left),
      right: Math.round(rect.right),
      width: Math.round(rect.width),
    })
  }
  // Only the outermost offender matters: a parent's overflow is the cause, the child
  // inside it is a symptom.
  const crossingSet = new Set(crossing.map((entry) => entry.el))
  const offenders = crossing
    .filter((entry) => {
      for (let p = entry.el.parentElement; p; p = p.parentElement) if (crossingSet.has(p)) return false
      return true
    })
    .map(({ path, left, right, width }) => ({ path, left, right, width }))

  const scrollers = []
  for (const el of document.querySelectorAll('body *')) {
    const style = getComputedStyle(el)
    if (style.overflowX === 'visible' || el.clientWidth === 0) continue
    if (el.scrollWidth > el.clientWidth + 1) {
      scrollers.push({
        path: describe(el),
        clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth,
        overflowX: style.overflowX,
        // A container that scrolls sideways with nothing to scroll is not a problem.
        visible: el.getBoundingClientRect().width > 0,
      })
    }
  }

  return {
    viewportWidth,
    viewportHeight: window.innerHeight,
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
    offenders,
    scrollers: scrollers.filter((entry) => entry.visible),
  }
}

/** Every measurement taken at one viewport, reported per surface. */
async function auditState(page, label, viewportName, { noPageScroll = false } = {}) {
  const measured = await page.evaluate(probe)
  const overflow = measured.scrollWidth - measured.viewportWidth
  const offenders = measured.offenders
    .map((entry) => `${entry.path} [${entry.left}..${entry.right}]`)
    .join(' | ')
  check(
    `${viewportName} · ${label}: no horizontal overflow`,
    overflow <= 1 && offenders === '',
    overflow > 1 ? `page is ${overflow}px too wide` : offenders,
  )
  if (noPageScroll) {
    // The app shell is exactly one viewport tall: the page itself must not scroll, or
    // the composer would drift off the bottom on a phone.
    const vertical = measured.scrollHeight - measured.viewportHeight
    check(
      `${viewportName} · ${label}: the shell fits the viewport height`,
      vertical <= 1,
      `${vertical}px taller than the viewport`,
    )
  }
  // Containers that scroll sideways with something in them are reported rather than
  // failed: some of them are the correct UX (a wide table would be), and the point is
  // that the list should stay short and deliberate.
  for (const scroller of measured.scrollers) {
    horizontalScrollers.set(
      `${scroller.path} (overflow-x: ${scroller.overflowX})`,
      `${viewportName} · ${label}: ${scroller.clientWidth}px wide, ${scroller.scrollWidth}px of content`,
    )
  }
}

/** Interactive elements smaller than this are hard to hit with a thumb. */
const MIN_TOUCH_TARGET = 36
const NO_PAGE_SCROLL = { noPageScroll: true }

async function auditTouchTargets(page, label) {
  const small = await page.evaluate((min) => {
    const found = []
    for (const el of document.querySelectorAll('button, a, input, select, textarea, [role="button"], [role="menuitem"]')) {
      const rect = el.getBoundingClientRect()
      if (rect.width < 1 || rect.height < 1) continue
      if (getComputedStyle(el).visibility === 'hidden') continue
      if (rect.width < min || rect.height < min) {
        const name = el.getAttribute('aria-label') ?? el.textContent?.trim().slice(0, 24) ?? el.tagName
        found.push(`${name} ${Math.round(rect.width)}×${Math.round(rect.height)}`)
      }
    }
    return found
  }, MIN_TOUCH_TARGET)
  check(`${label}: touch targets ≥ ${MIN_TOUCH_TARGET}px`, small.length === 0, small.slice(0, 8).join(', '))
}

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const username = `resp_${Date.now().toString(36)}`
const password = 'resp-password-123'
const LONG_TITLE = 'A-title-with-no-spaces-at-all-that-goes-on-and-on-0123456789'.repeat(3)
const LONG_WORD = 'W'.repeat(180)
// A real (tiny) PNG, so the crop step can actually be opened and measured.
const PROBE_IMAGE = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAIAQMAAAD+wSzIAAAABlBMVEX///+/v7+jQ3Y5AAAADklEQVQI12P4AIX8EAgALgAD/aNpbtEAAAAASUVORK5CYII=',
  'base64',
)

const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
const consoleErrors = []
page.on('pageerror', (error) => consoleErrors.push(error.message))

// -- Public surfaces: the auth page and the reset page --------------------------------

console.log('\n[ public pages ]')
await page.goto(APP_URL, { waitUntil: 'domcontentloaded' })
await page.waitForLoadState('load')

for (const viewport of VIEWPORTS) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height })
  await page.waitForTimeout(60)
  await auditState(page, 'sign in', viewport.name)
  await page.getByRole('tab', { name: 'Create account' }).click()
  await auditState(page, 'create account', viewport.name)
  await page.getByRole('tab', { name: 'Sign in' }).click()
}

await page.setViewportSize({ width: 390, height: 844 })
await page.getByRole('button', { name: 'Forgot password?' }).click()
await page.getByLabel('Username or email').waitFor({ timeout: 5000 })
for (const viewport of VIEWPORTS) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height })
  await page.waitForTimeout(60)
  await auditState(page, 'reset password', viewport.name)
}
await page.goBack()
await page.getByRole('button', { name: 'Forgot password?' }).waitFor({ timeout: 5000 })

// -- The app: register, make a conversation with awkward content ----------------------

console.log('\n[ app shell ]')
await page.setViewportSize({ width: 1280, height: 800 })
await page.getByRole('tab', { name: 'Create account' }).click()
await page.getByLabel('Username').fill(username)
await page.getByLabel('Password', { exact: true }).fill(password)
await page.getByLabel('Confirm password').fill(password)
await page.getByRole('button', { name: 'Create account' }).click()
await page.waitForSelector('aside', { timeout: 15000 })

// The empty desktop workspace — the first thing a wide-screen user sees, and the only
// state in which the chat pane has no conversation and no header.
for (const viewport of VIEWPORTS.filter((size) => size.width > 800)) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height })
  await page.waitForTimeout(60)
  await auditState(page, 'empty workspace', viewport.name, NO_PAGE_SCROLL)
}
await page.setViewportSize({ width: 1280, height: 800 })

await page.getByRole('button', { name: 'New chat' }).click()
await page.getByText('Who do you want to talk to?').waitFor({ timeout: 10000 })
const characterRadios = page.getByRole('radio')
await characterRadios.first().waitFor({ timeout: 10000 })
const characterName = (await characterRadios.first().innerText()).split(/\r?\n/).map((line) => line.trim()).filter(Boolean).pop()
await characterRadios.first().click()
await page.getByRole('button', { name: 'Next' }).click()
const modelRadios = page.getByRole('radio')
await modelRadios.first().waitFor({ timeout: 10000 })
await modelRadios.first().click()
await page.getByRole('button', { name: 'Next' }).click()
// The longest, least cooperative title the field accepts: it has to truncate, not push.
await page.getByLabel("Chat name (optional)").fill(LONG_TITLE)
await page.getByRole('button', { name: 'Start chatting' }).click()
await page.getByText(`Say hello to ${characterName}`).waitFor({ timeout: 10000 })

// A message that is deliberately hard to lay out: an unbroken run of characters with
// no space to wrap at.
const composer = page.getByLabel(`Message ${characterName}`)
await composer.fill(`${LONG_WORD} and a normal sentence after it.`)
await composer.press('Enter')
await page.getByText(LONG_WORD.slice(0, 40), { exact: false }).first().waitFor({ timeout: 15000 })
// A reply still streaming scrolls the list under everything the audit measures, so let
// it finish first (the mock/real provider answers in a few seconds).
await page
  .waitForFunction(() => !document.body.textContent?.includes('is typing'), { timeout: 60000 })
  .catch(() => undefined)

for (const viewport of VIEWPORTS) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height })
  await page.waitForTimeout(80)
  await auditState(page, 'chat', viewport.name, NO_PAGE_SCROLL)

  // The composer and its send button are the two controls that matter most here.
  const composerBox = await composer.boundingBox()
  const sendBox = await page.getByRole('button', { name: /^Send message/ }).boundingBox()
  const inside = (box) =>
    box !== null && box.x >= -1 && box.x + box.width <= viewport.width + 1 && box.y >= -1 && box.y + box.height <= viewport.height + 1
  check(`${viewport.name} · composer inside the viewport`, inside(composerBox), JSON.stringify(composerBox))
  check(`${viewport.name} · send button inside the viewport`, inside(sendBox), JSON.stringify(sendBox))

  // Narrow screens show the conversation list as a drawer: it has to fit too.
  if (viewport.width <= 800) {
    await page.getByRole('button', { name: 'Back to conversations' }).click()
    await page.waitForTimeout(120)
    await auditState(page, 'conversation drawer', viewport.name, NO_PAGE_SCROLL)
    await page.getByTitle(LONG_TITLE).first().click()
    await page.getByText(LONG_WORD.slice(0, 40), { exact: false }).first().waitFor({ timeout: 10000 })
  }
}

// -- Dialogs, at the sizes where they are most likely to break ------------------------

console.log('\n[ dialogs ]')
for (const viewport of VIEWPORTS.filter((size) => DETAIL_SIZES.has(size.name))) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height })
  await page.waitForTimeout(80)

  // Narrow screens keep the conversation list in a drawer, and "New chat" lives in it.
  const narrow = viewport.width <= 800
  const back = page.getByRole('button', { name: 'Back to conversations' })
  if (narrow && (await back.isVisible().catch(() => false))) {
    await back.click()
    await page.waitForTimeout(150)
  }

  // New chat wizard: three steps, each with its own layout.
  await page.getByRole('button', { name: 'New chat' }).click()
  await page.getByText('Who do you want to talk to?').waitFor({ timeout: 10000 })
  await auditState(page, 'new chat: character step', viewport.name, NO_PAGE_SCROLL)

  // The character form, opened from the wizard itself (its two-column rows are the
  // layout most likely to be written for a desktop dialog).
  await page.getByRole('button', { name: /New character/ }).click()
  await page.getByRole('dialog').last().waitFor({ timeout: 10000 })
  await auditState(page, 'character form', viewport.name, NO_PAGE_SCROLL)
  await page.getByRole('dialog').last().getByRole('button', { name: 'Close dialog' }).click()
  await page.waitForTimeout(150)

  const stepRadios = page.getByRole('radio')
  await stepRadios.first().click()
  await page.getByRole('button', { name: 'Next' }).click()
  await auditState(page, 'new chat: model step', viewport.name, NO_PAGE_SCROLL)
  const modelOptions = page.getByRole('radio')
  await modelOptions.first().click()
  await page.getByRole('button', { name: 'Next' }).click()
  await auditState(page, 'new chat: persona step', viewport.name, NO_PAGE_SCROLL)

  // The persona form, same reasoning as the character one.
  await page.getByRole('button', { name: /New persona/ }).click()
  await page.getByRole('dialog').last().waitFor({ timeout: 10000 })
  await auditState(page, 'persona form', viewport.name, NO_PAGE_SCROLL)
  await page.getByRole('dialog').last().getByRole('button', { name: 'Close dialog' }).click()
  await page.waitForTimeout(150)
  await page.getByRole('dialog').last().getByRole('button', { name: 'Close dialog' }).click()
  await page.waitForTimeout(150)

  // Settings: the longest form in the app, in all three of its shapes.
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await page.getByRole('heading', { name: 'Settings' }).waitFor({ timeout: 10000 })
  await auditState(page, 'settings: default form', viewport.name, NO_PAGE_SCROLL)

  // The mobile-number form: a fixed "+98" prefix beside an input.
  await page.getByRole('radio', { name: 'Text message' }).click()
  await auditState(page, 'settings: mobile number form', viewport.name, NO_PAGE_SCROLL)

  // The authenticator enrolment: a QR code, a monospace secret and a code field.
  await page.getByRole('radio', { name: 'Authenticator app' }).click()
  await page.getByRole('button', { name: 'Show QR code' }).click()
  try {
    await page.getByText(/Scan this with your authenticator app/).waitFor({ timeout: 10000 })
  } catch (error) {
    const text = await page.getByRole('dialog').last().innerText().catch(() => '(no dialog)')
    console.log(`  (enrolment panel did not open: ${text.slice(0, 240).replace(/\n/g, ' | ')})`)
    throw error
  }
  await auditState(page, 'settings: authenticator enrolment', viewport.name, NO_PAGE_SCROLL)
  await page.getByRole('button', { name: 'Cancel' }).click()
  await page.waitForTimeout(150)

  // The profile picture picker, mid-crop: the tallest thing in the dialog.
  await page.getByRole('button', { name: 'Choose image' }).click()
  await page.locator('input[type="file"]').setInputFiles({
    name: 'probe.png',
    mimeType: 'image/png',
    buffer: PROBE_IMAGE,
  })
  await page.getByRole('slider').waitFor({ timeout: 10000 }).catch(() => undefined)
  await auditState(page, 'settings: image crop', viewport.name, NO_PAGE_SCROLL)
  await page.getByRole('button', { name: 'Cancel' }).click()
  await page.waitForTimeout(150)
  await page.getByRole('dialog').last().getByRole('button', { name: 'Close dialog' }).click()
  await page.waitForTimeout(150)

  // Character profile, opened from the row that owns it.
  await page.getByRole('button', { name: `View ${characterName} profile` }).first().click()
  await page.getByRole('dialog').last().waitFor({ timeout: 10000 })
  await auditState(page, 'character profile', viewport.name, NO_PAGE_SCROLL)
  await page.getByRole('dialog').last().getByRole('button', { name: 'Close dialog' }).click()
  await page.waitForTimeout(150)

  // The delete confirmation: the one dialog that opens from a sidebar row.
  const row = page.locator('[role="listitem"]').first()
  await row.hover().catch(() => undefined)
  const deleteButton = row.getByRole('button', { name: /^Delete / })
  if (await deleteButton.isVisible().catch(() => false)) {
    await deleteButton.click()
    await page.getByRole('dialog').last().waitFor({ timeout: 10000 })
    await auditState(page, 'delete confirmation', viewport.name, NO_PAGE_SCROLL)
    await page.getByRole('button', { name: 'Cancel' }).click()
    await page.waitForTimeout(150)
  }

  // Back to the open chat, so the next size starts where this one did.
  if (narrow) {
    await page.getByTitle(LONG_TITLE).first().click()
    await page.getByText(LONG_WORD.slice(0, 40), { exact: false }).first().waitFor({ timeout: 10000 })
  }
}

// -- Floating layers: the context menu and the toast ----------------------------------
// Both are positioned against the viewport, so they are checked at the narrowest size
// (where there is no room to be wrong) and at a desktop size.

console.log('\n[ floating layers ]')
for (const viewport of [
  { name: '320x568 small phone', width: 320, height: 568 },
  { name: '1440x900 desktop', width: 1440, height: 900 },
]) {
  await page.setViewportSize({ width: viewport.width, height: viewport.height })
  await page.waitForTimeout(100)
  const narrow = viewport.width <= 800
  if (narrow) {
    await page.getByRole('button', { name: 'Back to conversations' }).click()
    await page.waitForTimeout(120)
    await page.getByTitle(LONG_TITLE).first().click()
    await page.getByText(LONG_WORD.slice(0, 40), { exact: false }).first().waitFor({ timeout: 10000 })
  }

  // Opened at the far right of a message: the menu has to be pushed back inside.
  const message = page.getByText(LONG_WORD.slice(0, 40), { exact: false }).first()
  const menu = page.getByRole('menu', { name: 'Message actions' })
  // Raw mouse events, retried: the row opens this menu from a native right-click, and
  // the pointer sequence has to land on the message itself.
  let opened = false
  for (let attempt = 0; attempt < 3 && !opened; attempt += 1) {
    await message.scrollIntoViewIfNeeded()
    await page.waitForTimeout(80)
    const box = await message.boundingBox()
    if (!box || box.y < 0 || box.y + box.height > viewport.height) break
    await page.mouse.move(box.x + box.width - 4, box.y + box.height / 2)
    await page.mouse.down({ button: 'right' })
    await page.mouse.up({ button: 'right' })
    opened = (await menu.count()) > 0
    if (!opened) await page.waitForTimeout(250)
  }
  check(`${viewport.name} · message menu opens on right-click`, opened)
  if (!opened) continue
  const menuBox = await menu.boundingBox()
  check(
    `${viewport.name} · message menu stays inside the viewport`,
    menuBox !== null && menuBox.x >= -1 && menuBox.x + menuBox.width <= viewport.width + 1,
    JSON.stringify(menuBox),
  )
  await auditState(page, 'message menu', viewport.name, NO_PAGE_SCROLL)

  // Copy pushes a toast, which is the app's other viewport-fixed layer.
  await page.getByRole('menuitem', { name: 'Copy' }).click()
  const toast = page.getByText('Copied to clipboard').first()
  await toast.waitFor({ timeout: 10000 })
  const toastBox = await toast.locator('..').boundingBox()
  check(
    `${viewport.name} · toast stays inside the viewport`,
    toastBox !== null && toastBox.x >= -1 && toastBox.x + toastBox.width <= viewport.width + 1,
    JSON.stringify(toastBox),
  )
  await auditState(page, 'toast', viewport.name, NO_PAGE_SCROLL)
  await page.waitForTimeout(3200)
}

// -- Touch: affordances that only exist on hover, and thumb-sized targets ------------

console.log('\n[ touch ]')
const touch = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
await touch.goto(APP_URL, { waitUntil: 'domcontentloaded' })
await touch.waitForLoadState('load')
await touch.getByRole('tab', { name: 'Sign in' }).click()
await touch.getByLabel('Username').fill(username)
await touch.getByLabel('Password', { exact: true }).fill(password)
await touch.getByRole('button', { name: 'Sign in' }).click()
await touch.waitForSelector('aside', { timeout: 15000 })

check('touch: hover media query is off', await touch.evaluate(() => matchMedia('(hover: none)').matches))
const renameButton = touch.getByRole('button', { name: /^Rename / }).first()
// `isVisible()` ignores opacity, so this asks what a thumb would actually find: is the
// row's action layer opaque and able to receive a tap?
const renameReachable = await renameButton
  .evaluate((el) => {
    const layer = getComputedStyle(el.parentElement ?? el)
    return Number(layer.opacity) > 0.05 && layer.pointerEvents !== 'none'
  })
  .catch(() => false)
check('touch: the conversation row actions are reachable without hover', renameReachable)
await auditTouchTargets(touch, 'touch (drawer)')

await touch.getByTitle(LONG_TITLE).first().click()
await touch.getByText(LONG_WORD.slice(0, 40), { exact: false }).first().waitFor({ timeout: 15000 })
await auditState(touch, 'chat', '390x844 touch', NO_PAGE_SCROLL)
await auditTouchTargets(touch, 'touch (chat)')

// The dialogs a thumb has to work inside as well.
await touch.getByRole('button', { name: 'Back to conversations' }).click()
await touch.waitForTimeout(150)
await touch.getByRole('button', { name: 'New chat' }).click()
await touch.getByText('Who do you want to talk to?').waitFor({ timeout: 10000 })
await auditTouchTargets(touch, 'touch (new chat dialog)')
await touch.getByRole('dialog').last().getByRole('button', { name: 'Close dialog' }).click()
await touch.waitForTimeout(150)
await touch.getByRole('button', { name: 'Settings', exact: true }).click()
await touch.getByRole('heading', { name: 'Settings' }).waitFor({ timeout: 10000 })
await auditTouchTargets(touch, 'touch (settings dialog)')
await touch.close()

// -- Cleanup (while the account still signs in with a password alone) ------------------

const items = page.locator('[role="listitem"]')
const count = await items.count()
for (let index = 0; index < count; index += 1) {
  const item = items.first()
  await item.hover()
  const deleteButton = item.getByRole('button', { name: /^Delete / })
  if (await deleteButton.isVisible().catch(() => false)) {
    await deleteButton.click()
    await page.getByRole('button', { name: 'Delete', exact: true }).click()
    await page.waitForTimeout(400)
  }
}

// -- The second-factor and recovery screens -------------------------------------------
// The two screens a signed-out phone user is most likely to be stranded on, and the
// only ones that cannot be reached without a code. An authenticator enrolment makes
// them reachable without sending anything to anyone.

console.log('\n[ second factor and recovery ]')

/** Measure the screen that is open right now at every viewport, without navigating.
 *
 *  These are signed-out pages rather than the app shell, so they are *allowed* to be
 *  taller than a short landscape viewport and scroll — there is no composer to keep in
 *  view. What must not happen is horizontal overflow, which is still checked. */
async function auditCurrentStateEverywhere(label) {
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.waitForTimeout(60)
    await auditState(page, label, viewport.name)
  }
  await page.setViewportSize({ width: 390, height: 844 })
}

// Enrol an authenticator through the settings dialog: the QR panel's own happy path.
await page.getByRole('button', { name: 'Back to conversations' }).click().catch(() => undefined)
await page.waitForTimeout(150)
await page.getByRole('button', { name: 'Settings', exact: true }).click()
await page.getByRole('heading', { name: 'Settings' }).waitFor({ timeout: 10000 })
await page.getByRole('radio', { name: 'Authenticator app' }).click()
await page.getByRole('button', { name: 'Show QR code' }).click()
await page.getByText(/Scan this with your authenticator app/).waitFor({ timeout: 10000 })
const secret = (await page.locator('code').first().innerText()).trim()
await page.getByLabel('Enter the code your app shows').fill(totp(secret))
await page.getByRole('button', { name: 'Confirm' }).click()
await page.getByText('codes come from your authenticator app').waitFor({ timeout: 10000 })
check('settings: the authenticator enrols and becomes the default', true)
await page.getByRole('dialog').last().getByRole('button', { name: 'Close dialog' }).click()
await page.waitForTimeout(200)

// Signed out, the sign-in second step: the code prompt the phone user gets.
await page.getByRole('button', { name: 'Sign out' }).click()
await page.getByRole('button', { name: 'Sign in', exact: true }).waitFor({ timeout: 10000 })
await page.getByLabel('Username').fill(username)
await page.getByLabel('Password', { exact: true }).fill(password)
await page.getByRole('button', { name: 'Sign in', exact: true }).click()
await page.getByLabel('Authenticator code').waitFor({ timeout: 15000 })
await auditCurrentStateEverywhere('sign-in code step')
await page.getByLabel('Authenticator code').fill(totp(secret))
await page.getByRole('button', { name: 'Verify and sign in' }).click()
await page.waitForSelector('aside', { timeout: 15000 })
check('sign-in code step completes with an authenticator code', true)

// The recovery flow: identify, choose the authenticator, then the code and password
// steps. "Start over" at the end, so this audit never changes a password.
await page.getByRole('button', { name: 'Sign out' }).click()
await page.getByRole('button', { name: 'Forgot password?' }).waitFor({ timeout: 10000 })
await page.getByRole('button', { name: 'Forgot password?' }).click()
await page.getByLabel('Username or email').fill(username)
await page.getByRole('button', { name: 'Continue' }).click()
await page.getByRole('button', { name: 'Authenticator app' }).waitFor({ timeout: 10000 })
await page.getByRole('button', { name: 'Authenticator app' }).click()
await page.getByLabel('Authenticator code').waitFor({ timeout: 10000 })
await auditCurrentStateEverywhere('recovery code step')
await page.getByLabel('Authenticator code').fill(totp(secret))
await page.getByRole('button', { name: 'Verify code' }).click()
await page.getByLabel('New password', { exact: true }).waitFor({ timeout: 10000 })
await auditCurrentStateEverywhere('recovery password step')
await page.getByRole('button', { name: 'Start over' }).click()
await page.getByLabel('Username or email').waitFor({ timeout: 10000 })
check('recovery returns to the first step on "Start over"', true)



check('no uncaught page errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))

if (horizontalScrollers.size > 0) {
  console.log('\ncontainers that scroll horizontally (intentional or not):')
  for (const [element, where] of horizontalScrollers) console.log(`  ${element} — ${where}`)
}

const failed = results.filter((result) => !result.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
if (failed.length > 0) {
  console.log('\nfailures:')
  for (const failure of failed) console.log(`  - ${failure.name}${failure.extra ? ` — ${failure.extra}` : ''}`)
}
console.log(`\naccount created for this run: ${username}`)
await browser.close()
process.exit(failed.length > 0 ? 1 : 0)
