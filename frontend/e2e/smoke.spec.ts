import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Zugangsdaten nur aus der Umgebung (CI), nie ausgeben.
const { MONI_EMAIL, MONI_PASSWORD, MONI_RECOVERY } = process.env
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']

declare global {
  interface Window {
    __cspVerstoesse: string[]
  }
}

async function axe(page: Page, wo: string) {
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze()
  expect(violations.map((v) => `${wo}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target.join(' ')}`)).toEqual([])
}

test('Oberfläche läuft unter der CSP, per Tastatur und ohne axe-Verstöße', async ({ page }) => {
  test.skip(!MONI_EMAIL || !MONI_PASSWORD || !MONI_RECOVERY, 'MONI_EMAIL, MONI_PASSWORD und MONI_RECOVERY nötig')
  const konsole: string[] = []
  page.on('console', (m) => m.type() === 'error' && konsole.push(m.text()))
  page.on('pageerror', (e) => konsole.push(e.message))
  await page.addInitScript(() => {
    window.__cspVerstoesse = []
    document.addEventListener('securitypolicyviolation', (e) => {
      window.__cspVerstoesse.push(`${e.violatedDirective} ${e.blockedURI} ${e.sourceFile}:${e.lineNumber}`)
    })
  })

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
  konsole.length = 0 // vor der Anmeldung meldet der Browser das erwartete 401 der Sitzungsabfrage

  // Tastatur: erster Tab-Stopp ist der Sprunglink, Enter springt in main
  await page.reload() // frischer Fokus-Startpunkt (nach dem Routenwechsel steht der Fokus auf der H1)
  await expect(page.getByText('Manager · lokal')).toBeVisible()
  await page.keyboard.press('Tab')
  const sprunglink = page.getByRole('link', { name: 'Zum Inhalt springen' })
  await expect(sprunglink).toBeFocused()
  await page.keyboard.press('Enter')
  await expect.poll(() => page.evaluate(() => !!document.activeElement?.closest('main'))).toBe(true)

  // Serverdetail mit Diagrammen
  const seiten = [
    { wo: 'Übersicht', oeffnen: async () => { await page.getByRole('link', { name: 'Übersicht', exact: true }).first().click(); await expect(page.getByText('Manager · lokal')).toBeVisible() } },
    {
      wo: 'Serverdetail',
      oeffnen: async () => {
        await page.getByText('Manager · lokal').click()
        await expect(page).toHaveURL(/\/server\/\d+$/)
        const bilder = page.locator('figure [role="img"]')
        await expect(bilder.first()).toBeVisible()
        await expect(bilder.first().locator('svg').first()).toBeVisible()
        await expect(page.getByText('Diese Seite konnte nicht geladen werden.')).toHaveCount(0)
      },
    },
    { wo: 'Alarme', oeffnen: async () => { await page.getByRole('link', { name: 'Alarme' }).first().click(); await expect(page.getByRole('heading', { level: 1, name: 'Alarme' })).toBeVisible() } },
  ]
  for (const theme of ['Hell', 'Dunkel']) {
    await page.getByRole('button', { name: theme, exact: true }).click()
    for (const s of seiten) {
      await s.oeffnen()
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme === 'Hell' ? 'light' : 'dark')
      await axe(page, `${s.wo} (${theme})`)
    }
  }

  expect(await page.evaluate(() => window.__cspVerstoesse)).toEqual([])
  expect(konsole).toEqual([])

  // Abmelden
  await page.getByRole('button', { name: 'Abmelden' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page).toHaveURL(/\/login/)
})
