import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

function preferenceScript(): string {
  const module = readFileSync(resolve(process.cwd(), 'src/web/themes.py'), 'utf8')
  const match = module.match(/THEME_PREFERENCE_SCRIPT = """([\s\S]*?)"""/)
  if (!match) throw new Error('THEME_PREFERENCE_SCRIPT not found in src/web/themes.py')
  return match[1]
}

const NORD = '/static/themes/nord/colors.css'
const SNOWSTORM = '/static/themes/snowstorm/colors.css'
const CHOICES = JSON.stringify({
  dark: { id: 'nord', href: NORD },
  light: { id: 'snowstorm', href: SNOWSTORM },
})

function paintedNord(source: 'stored' | 'default', prefersLight: boolean): HTMLLinkElement {
  const root = document.documentElement
  root.dataset.theme = 'nord'
  root.dataset.themeType = 'dark'
  root.dataset.themeSource = source
  root.dataset.themeChoices = CHOICES

  const link = document.createElement('link')
  link.id = 'theme-stylesheet'
  link.rel = 'stylesheet'
  link.setAttribute('href', NORD)
  document.head.appendChild(link)

  window.matchMedia = ((query: string) => ({
    matches: prefersLight === query.includes('light'),
  })) as unknown as typeof window.matchMedia

  return link
}

function run(): void {
  new Function(preferenceScript())()
}

describe('the theme the shell resolves before first paint', () => {
  beforeEach(() => {
    for (const key of ['theme', 'themeType', 'themeSource', 'themeChoices']) {
      delete document.documentElement.dataset[key]
    }
  })

  afterEach(() => {
    document.getElementById('theme-stylesheet')?.remove()
  })

  it('paints the light theme when nothing is picked and the OS asks for light', () => {
    const link = paintedNord('default', true)

    run()

    expect(link.getAttribute('href')).toBe(SNOWSTORM)
    expect(document.documentElement.dataset.theme).toBe('snowstorm')
    expect(document.documentElement.dataset.themeType).toBe('light')
  })

  it('leaves the href alone when the OS already agrees, so nothing refetches', () => {
    const link = paintedNord('default', false)
    const rewrite = vi.spyOn(link, 'setAttribute')

    run()

    expect(rewrite).not.toHaveBeenCalled()
    expect(document.documentElement.dataset.theme).toBe('nord')
  })

  it('never overrides a theme the user picked, whatever the OS asks for', () => {
    const link = paintedNord('stored', true)

    run()

    expect(link.getAttribute('href')).toBe(NORD)
    expect(document.documentElement.dataset.theme).toBe('nord')
  })

  it('keeps the default when a second theme of the OS kind is installed', () => {
    const link = paintedNord('default', false)
    document.documentElement.dataset.themeChoices = JSON.stringify({
      dark: { id: 'dracula', href: '/static/private-themes/dracula/colors.css' },
      light: { id: 'snowstorm', href: SNOWSTORM },
    })

    run()

    expect(link.getAttribute('href')).toBe(NORD)
    expect(document.documentElement.dataset.theme).toBe('nord')
  })

  it('leaves the paint alone when no theme of the preferred kind is installed', () => {
    const link = paintedNord('default', true)
    document.documentElement.dataset.themeChoices = JSON.stringify({
      dark: { id: 'nord', href: NORD },
    })

    run()

    expect(link.getAttribute('href')).toBe(NORD)
    expect(document.documentElement.dataset.theme).toBe('nord')
  })
})
