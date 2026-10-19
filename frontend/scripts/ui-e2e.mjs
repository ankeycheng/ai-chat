// Headless-Chrome UI end-to-end check + screenshots (desktop 1440x900, mobile 390x844).
// Usage: node scripts/ui-e2e.mjs [baseUrl] [outDir]
import puppeteer from 'puppeteer-core'
import fs from 'node:fs'

const BASE = process.argv[2] ?? 'http://127.0.0.1:5173'
const OUT = process.argv[3] ?? '../docs/screenshots'
const CHROME = process.env.CHROME_PATH ?? '/usr/bin/google-chrome'
fs.mkdirSync(OUT, { recursive: true })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const checks = {}

async function waitIdle(page, timeout = 240000) {
  const t0 = Date.now()
  await sleep(800)
  while (Date.now() - t0 < timeout) {
    const busy = await page.evaluate(() => {
      const streamingCursor = [...document.querySelectorAll('.msg-assistant .markdown')].some((el) => el.textContent?.includes('▍'))
      return !!document.querySelector('.typing') || streamingCursor || !!document.querySelector('.send .spin')
    })
    if (!busy) return true
    await sleep(500)
  }
  throw new Error('timeout waiting for generation to finish')
}

async function setTheme(page, t) {
  await sleep(1500) // let the app mount first (its effect persists the current preference)
  await page.evaluate((v) => localStorage.setItem('theme', v), t)
  await page.reload({ waitUntil: 'load' })
  await sleep(1500)
}

async function typeAndSend(page, text) {
  await page.waitForSelector('.composer textarea')
  await page.click('.composer textarea')
  await page.type('.composer textarea', text, { delay: 2 })
  await page.click('.send')
}

async function setToggle(page, label, on) {
  const pressed = await page.$$eval('.pill', (els, l) => els.find((e) => e.textContent.includes(l))?.getAttribute('aria-pressed'), label)
  if ((pressed === 'true') !== on) {
    const btns = await page.$$('.pill')
    for (const b of btns) if ((await b.evaluate((e) => e.textContent)).includes(label)) await b.click()
  }
}

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  protocolTimeout: 120000,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--lang=zh-CN', '--font-render-hinting=none'],
})
try {
  const ctx = await browser.createBrowserContext()
  const a = await ctx.newPage()
  await a.setViewport({ width: 1440, height: 900 })
  await a.goto(BASE, { waitUntil: 'load' })
  await sleep(1500)
  await a.evaluate(() => localStorage.clear())
  await setTheme(a, 'dark')
  await a.click('.new-chat')
  await sleep(300)
  await a.screenshot({ path: `${OUT}/desktop-empty-dark.png` })

  // Turn 1: thinking + web search
  await setToggle(a, '深度思考', true)
  await setToggle(a, '联网搜索', true)
  await typeAndSend(a, 'DeepSeek 最近开放了哪些新的 API 能力？请简要说明，并给出一个调用其搜索接口的 Python 示例代码。')
  await waitIdle(a)
  checks.turn1_sources = (await a.$$('.source-chip')).length > 0
  checks.turn1_reasoning_block = !!(await a.$('.reasoning-toggle'))
  checks.turn1_code_block = !!(await a.$('.code-block'))

  // Window B (same browser, separate tab): list sync + open same conversation
  const b = await ctx.newPage()
  await b.setViewport({ width: 1280, height: 800 })
  await b.goto(BASE, { waitUntil: 'load' })
  await b.bringToFront()
  await sleep(1500)
  const titleA = await a.$eval('.chat-title', (e) => e.textContent)
  const sideTitlesB = await b.$$eval('.side-title', (els) => els.map((e) => e.textContent))
  checks.window_b_sees_title = sideTitlesB.includes(titleA) && titleA !== '新对话'
  checks.auto_title = titleA
  await b.evaluate((t) => { [...document.querySelectorAll('.side-item')].find((e) => e.textContent.includes(t))?.click() }, titleA)
  await sleep(1500)

  // Turn 2 sent from window B, observed live in window A (multi-window message sync)
  await setToggle(b, '深度思考', false)
  await setToggle(b, '联网搜索', false)
  await typeAndSend(b, '我叫安琪。请用一句话总结你上面的回答，并称呼我的名字。')
  await sleep(2500)
  const aSawUser = await a.$$eval('.msg-user .bubble', (els) => els.some((e) => e.textContent.includes('我叫安琪')))
  checks.window_a_receives_user_message_live = aSawUser
  await waitIdle(b)
  await waitIdle(a)
  const countA = await a.$$eval('.msg', (els) => els.length)
  const countB = await b.$$eval('.msg', (els) => els.length)
  const lastA = await a.$$eval('.msg-assistant .markdown', (els) => els.at(-1)?.textContent)
  const lastB = await b.$$eval('.msg-assistant .markdown', (els) => els.at(-1)?.textContent)
  checks.windows_same_messages = countA === countB && countA >= 4 && lastA === lastB
  checks.context_recall = (lastA ?? '').includes('安琪')

  await a.bringToFront()
  await a.evaluate(() => window.scrollTo(0, 0))
  await a.screenshot({ path: `${OUT}/desktop-chat-dark.png` })
  // expanded thinking block
  await a.evaluate(() => document.querySelector('.reasoning-toggle')?.click())
  await a.evaluate(() => { const el = document.querySelector('.chat-scroll'); el.scrollTop = 0 })
  await sleep(300)
  await a.screenshot({ path: `${OUT}/desktop-thinking-expanded-dark.png` })
  await a.evaluate(() => document.querySelector('.reasoning-toggle')?.click())

  // light theme
  await b.close()
  await setTheme(a, 'light')
  await a.evaluate(() => { const el = document.querySelector('.chat-scroll'); el.scrollTop = el.scrollHeight })
  await sleep(300)
  await a.screenshot({ path: `${OUT}/desktop-chat-light.png` })

  // sidebar search (content match)
  await a.type('.side-search input', '安琪')
  await sleep(1200)
  checks.sidebar_search_hits = (await a.$$('.side-list .side-item')).length
  await a.screenshot({ path: `${OUT}/desktop-search-light.png` })

  // mobile (close other tabs: HTTP/1.1 dev server → max 6 connections per origin)
  await a.close()
  const m = await ctx.newPage()
  await m.emulate({
    viewport: { width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true },
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
  })
  await m.bringToFront()
  await m.goto(BASE, { waitUntil: 'load' })
  await setTheme(m, 'dark')
  checks.mobile_sidebar_hidden = await m.$eval('.sidebar-wrap', (e) => e.getBoundingClientRect().right <= 1)
  await m.evaluate(() => { const el = document.querySelector('.chat-scroll'); el.scrollTop = el.scrollHeight })
  await sleep(300)
  await m.screenshot({ path: `${OUT}/mobile-chat-dark.png` })
  await m.evaluate(() => { const el = document.querySelector('.chat-scroll'); el.scrollTop = 0 })
  await sleep(300)
  await m.screenshot({ path: `${OUT}/mobile-chat-top-dark.png` })
  await m.click('.chat-head .icon-btn')
  await sleep(500)
  checks.mobile_drawer_opens = await m.$eval('.sidebar-wrap', (e) => e.getBoundingClientRect().left >= -1)
  await m.screenshot({ path: `${OUT}/mobile-drawer-dark.png` })
  checks.mobile_no_horizontal_overflow = await m.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
  await m.click('.scrim')
  await sleep(400)
  await setTheme(m, 'light')
  await m.evaluate(() => { const el = document.querySelector('.chat-scroll'); el.scrollTop = el.scrollHeight })
  await sleep(300)
  await m.screenshot({ path: `${OUT}/mobile-chat-light.png` })
} finally {
  await browser.close()
}
checks.all_passed = Object.entries(checks).every(([k, v]) => k === 'auto_title' || k === 'sidebar_search_hits' ? !!v : v === true)
console.log(JSON.stringify(checks, null, 2))
process.exit(checks.all_passed ? 0 : 1)
