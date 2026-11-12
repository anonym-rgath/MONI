import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Zugangsdaten nur aus der Umgebung (CI), nie ausgeben.
const { MONI_EMAIL, MONI_PASSWORD, MONI_RECOVERY } = process.env
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']

async function axe(page: Page, wo: string) {
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze()
  expect(violations.map((v) => `${wo}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target.join(' ')}`)).toEqual([])
}

// CSP-Verstöße gehen sofort an Node und überstehen so Neuladen und Seitenwechsel.
async function csp(page: Page): Promise<string[]> {
  const verstoesse: string[] = []
  await page.exposeBinding('__meldeCsp', (_, v: string) => void verstoesse.push(v))
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) => {
      ;(window as unknown as { __meldeCsp: (v: string) => void }).__meldeCsp(`${e.violatedDirective} ${e.blockedURI} ${e.sourceFile}:${e.lineNumber}`)
    })
  })
  return verstoesse
}

test('Die Erfassung meldet einen absichtlichen CSP-Verstoß', async ({ page }) => {
  const verstoesse = await csp(page)
  await page.route('https://csp-probe.invalid/', (r) =>
    r.fulfill({ contentType: 'text/html', headers: { 'Content-Security-Policy': "script-src 'none'" }, body: '<script>1</script>' }),
  )
  await page.goto('https://csp-probe.invalid/')
  await expect.poll(() => verstoesse.length).toBeGreaterThan(0)
})

test('Oberfläche läuft unter der CSP, per Tastatur und ohne axe-Verstöße', async ({ page }) => {
  if (!MONI_EMAIL || !MONI_PASSWORD || !MONI_RECOVERY) throw new Error('MONI_EMAIL, MONI_PASSWORD und MONI_RECOVERY fehlen')
  const konsole: string[] = []
  page.on('console', (m) => {
    // einzig erwartet: das 401 der ersten Sitzungsabfrage vor der Anmeldung
    const erwartet401 = m.text().includes('401') && m.location().url.endsWith('/api/v1/auth/session')
    if (m.type() === 'error' && !erwartet401) konsole.push(m.text())
  })
  page.on('pageerror', (e) => konsole.push(e.message))
  const verstoesse = await csp(page)

  // Anmeldung
  await page.goto('/')
  await page.getByLabel('E-Mail-Adresse').fill(MONI_EMAIL!)
  await page.getByLabel('Passwort').fill(MONI_PASSWORD!)
  await page.getByRole('button', { name: 'Weiter' }).click()
  await page.getByRole('button', { name: 'Stattdessen Wiederherstellungscode verwenden' }).click()
  await page.getByLabel('Wiederherstellungscode').fill(MONI_RECOVERY!)
  await page.getByRole('button', { name: 'Anmelden' }).click()

  // Übersicht mit dem lokalen Server
  await expect(page.getByRole('heading', { level: 1, name: 'Übersicht' })).toBeVisible()
  await expect(page.getByText('Manager · lokal')).toBeVisible()

  // Tastatur: erster Tab-Stopp ist der Sprunglink, Enter springt in main
  await page.reload() // frischer Fokus-Startpunkt (nach dem Routenwechsel steht der Fokus auf der H1)
  await expect(page.getByText('Manager · lokal')).toBeVisible()
  await page.keyboard.press('Tab')
  const sprunglink = page.getByRole('link', { name: 'Zum Inhalt springen' })
  await expect(sprunglink).toBeFocused()
  await page.keyboard.press('Enter')
  await expect.poll(() => page.evaluate(() => !!document.activeElement?.closest('main'))).toBe(true)

  const verwaltung = (link: string, h1: string) => ({ wo: `Verwaltung: ${h1}`, oeffnen: async () => {
    await page.getByRole('navigation', { name: 'Bereiche der Verwaltung' }).getByRole('link', { name: link, exact: true }).click()
    await expect(page.getByRole('heading', { level: 1, name: h1 })).toBeVisible()
  } })

  // Serverdetail mit Diagrammen
  const seiten = [
    { wo: 'Übersicht', oeffnen: async () => { await page.getByRole('link', { name: 'Übersicht', exact: true }).first().click(); await expect(page.getByText('Manager · lokal')).toBeVisible() } },
    {
      wo: 'Serverdetail',
      oeffnen: async () => {
        await page.getByText('Manager · lokal').click()
        await expect(page).toHaveURL(/\/server\/\d+$/)
        // Standardansicht 24 Std.: "auto" fällt auf frischer Installation auf Rohpunkte zurück (P6-R14)
        await expect(page.getByRole('button', { name: '24 Std.' })).toHaveAttribute('aria-pressed', 'true')
        const bilder = page.locator('figure [role="img"]')
        await expect(bilder.first()).toBeVisible()
        await expect(bilder.first().locator('svg').first()).toBeVisible()
        await expect(page.getByText('Diese Seite konnte nicht geladen werden.')).toHaveCount(0)
      },
    },
    { wo: 'Alarme', oeffnen: async () => { await page.getByRole('link', { name: 'Alarme' }).first().click(); await expect(page.getByRole('heading', { level: 1, name: 'Alarme' })).toBeVisible() } },
    // nur lesen: Konto (samt erstem Schritt von "Authenticator wechseln", der noch keinen Code verbraucht) und die Verwaltung
    {
      wo: 'Konto',
      oeffnen: async () => {
        await page.locator('header a[href="/konto"]').click()
        await expect(page.getByRole('heading', { level: 1, name: 'Konto' })).toBeVisible()
      },
    },
    {
      wo: 'Konto: Authenticator wechseln',
      oeffnen: async () => {
        await page.getByRole('button', { name: 'Authenticator wechseln' }).click()
        await expect(page.getByLabel('Aktueller Code')).toBeVisible()
      },
    },
    { wo: 'Verwaltung: Server', oeffnen: async () => { await page.getByRole('link', { name: 'Verwaltung', exact: true }).click(); await expect(page.getByRole('heading', { level: 1, name: 'Server verwalten' })).toBeVisible() } },
    verwaltung('Benutzer', 'Benutzer'),
    verwaltung('API-Tokens', 'API-Tokens'),
    verwaltung('Audit-Protokoll', 'Audit-Protokoll'),
    verwaltung('Anmeldung', 'Anmeldung'),
  ]
  for (const theme of ['Hell', 'Dunkel']) {
    await page.getByRole('button', { name: theme, exact: true }).click()
    for (const s of seiten) {
      await s.oeffnen()
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme === 'Hell' ? 'light' : 'dark')
      await axe(page, `${s.wo} (${theme})`)
    }
  }

  expect(konsole).toEqual([])

  // Abmelden
  await page.getByRole('button', { name: 'Abmelden' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page).toHaveURL(/\/login/)

  expect(verstoesse).toEqual([]) // über alle Seitenladungen, auch Anmeldeseite und Neuladen
})
