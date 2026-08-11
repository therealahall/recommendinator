import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import OAuthConnectFlow from './OAuthConnectFlow.vue'

// Not "gog": the flow belongs to the source being connected, and a second GOG
// source is exactly the configuration that duplicated ids break. `gog_work` and
// `gog-work` are the realistic collision — both are valid source ids.
const SOURCE_IDS = ['gog', 'gog_work', 'gog-work']

function mountFlow(sourceId: string) {
  return mount(OAuthConnectFlow, {
    props: {
      sourceId,
      authUrl: 'https://login.gog.com/auth',
      expectedOrigin: 'https://login.gog.com',
      helpText: 'Paste the redirect URL after logging in:',
      serviceName: 'GOG Account',
    },
    attachTo: document.body,
  })
}

async function openCodeStep(sourceId: string) {
  const wrapper = mountFlow(sourceId)
  await wrapper.get('button').trigger('click')
  return wrapper
}

describe('OAuthConnectFlow', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('gives each source its own code field id and label association', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null)

    const wrappers = await Promise.all(SOURCE_IDS.map(openCodeStep))
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

    // A copy here would put the panel's words on screen and into the
    // accessibility tree twice, and would be unmounted before announcing.
    expect(wrapper.find('[aria-live]').exists()).toBe(false)
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
