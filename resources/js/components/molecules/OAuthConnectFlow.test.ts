import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import OAuthConnectFlow from './OAuthConnectFlow.vue'

// Not "gog": the flow belongs to the source being connected, and a second GOG
// source is exactly the configuration that duplicated ids break. `gog_work` and
// `gog-work` are the realistic collision — both are valid source ids.
const SOURCE_IDS = ['gog', 'gog_work', 'gog-work']

function mountFlow(sourceId: string, sourceName = `GOG (${sourceId})`) {
  return mount(OAuthConnectFlow, {
    props: {
      sourceId,
      sourceName,
      authUrl: 'https://login.gog.com/auth',
      expectedOrigin: 'https://login.gog.com',
      helpText: 'Paste the redirect URL after logging in:',
      serviceName: 'GOG Account',
    },
    attachTo: document.body,
  })
}

async function openCodeStep(sourceId: string, sourceName?: string) {
  const wrapper = mountFlow(sourceId, sourceName)
  await wrapper.get('button').trigger('click')
  return wrapper
}

describe('OAuthConnectFlow', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('gives each source its own code field id and label association', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null)

    const wrappers = await Promise.all(SOURCE_IDS.map((id) => openCodeStep(id)))
    const ids = wrappers.map((w) => w.get('input').attributes('id'))

    // Both panels sit in one document, so a shared id would resolve every
    // `for` to the first input: the second field loses its accessible name.
    expect(new Set(ids).size).toBe(SOURCE_IDS.length)
    for (const [index, wrapper] of wrappers.entries()) {
      expect(wrapper.get('label').attributes('for')).toBe(ids[index])
      expect(document.querySelectorAll(`#${ids[index]}`)).toHaveLength(1)
      wrapper.unmount()
    }
  })

  it('does not open an auth URL from an unexpected origin', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const wrapper = mount(OAuthConnectFlow, {
      props: {
        sourceId: 'gog_work',
        sourceName: 'GOG (work)',
        authUrl: 'https://evil.example.com/auth',
        expectedOrigin: 'https://login.gog.com',
        helpText: '',
        serviceName: 'GOG Account',
      },
    })

    await wrapper.get('button').trigger('click')

    expect(open).not.toHaveBeenCalled()
    expect(wrapper.find('input').exists()).toBe(false)
  })

  it('emits the trimmed code and clears the field', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null)
    const wrapper = await openCodeStep('gog_work')

    await wrapper.get('input').setValue('  auth-code  ')
    await wrapper.findAll('button')[1].trigger('click')

    expect(wrapper.emitted('submit')).toEqual([['auth-code']])
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })

  it('renders no status of its own, so the panel is the only voice', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null)
    const wrapper = await openCodeStep('gog_work')

    // Anchor: without it, a refused openAuth that never mounts the code step
    // satisfies both absences below.
    expect(wrapper.find('input').exists()).toBe(true)
    // A copy here would put the panel's words on screen and into the
    // accessibility tree twice, and would be unmounted before announcing.
    expect(wrapper.find('[aria-live]').exists()).toBe(false)
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('names both buttons and the code field for their source', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null)
    const work = await openCodeStep('gog_work', 'GOG (work)')
    const home = await openCodeStep('gog_home', 'GOG (home)')

    // Two expanded GOG panels: the enclosing group tells them apart on Tab,
    // but NVDA's and JAWS's element lists are name-only.
    const names = (wrapper: typeof work) => [
      wrapper.findAll('button')[0].attributes('aria-label'),
      wrapper.findAll('button')[1].attributes('aria-label'),
      wrapper.get('label').text(),
    ]
    expect(names(work)).toEqual([
      'Connect GOG Account for GOG (work)',
      'Connect GOG (work) with the pasted code',
      'GOG Account authorization code for GOG (work)',
    ])
    expect(new Set([...names(work), ...names(home)]).size).toBe(6)
    // Each visible label stays inside its accessible name (WCAG 2.5.3).
    expect(names(work)[0]).toContain(work.findAll('button')[0].text())
    expect(names(work)[1]).toContain(work.findAll('button')[1].text())
    work.unmount()
    home.unmount()
  })
})
