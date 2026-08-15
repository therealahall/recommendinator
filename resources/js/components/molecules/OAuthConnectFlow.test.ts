import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import OAuthConnectFlow from './OAuthConnectFlow.vue'

// The parent picks the wording, so these tests only follow it through. A
// sentence no branch of that decision produces is what proves the pass-through.
const HINT = 'The remedy the parent worked out.'

function mountFlow(sourceId: string, sourceName = `GOG (${sourceId})`) {
  return mount(OAuthConnectFlow, {
    props: {
      sourceId,
      sourceName,
      authUrl: 'https://login.gog.com/auth',
      expectedOrigin: 'https://login.gog.com',
      helpText: 'Paste the redirect URL after logging in:',
      serviceName: 'GOG Account',
      connectHint: HINT,
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
        connectHint: HINT,
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

  it('opens nothing when the reachable Connect is activated without an auth URL', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    const wrapper = mount(OAuthConnectFlow, {
      props: {
        sourceId: 'gog_work',
        sourceName: 'GOG (work)',
        authUrl: null,
        expectedOrigin: 'https://login.gog.com',
        helpText: '',
        serviceName: 'GOG Account',
        connectHint: HINT,
      },
      attachTo: document.body,
    })

    // aria-disabled does not block activation the way `disabled` did, so the
    // null-URL guard in openAuth is now the only thing refusing this click.
    await wrapper.get('button').trigger('click')

    expect(open).not.toHaveBeenCalled()
    expect(wrapper.find('input').exists()).toBe(false)
    wrapper.unmount()
  })
})
