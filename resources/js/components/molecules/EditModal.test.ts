import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import EditModal from './EditModal.vue'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({ get: vi.fn() }),
}))

const defaultItem = {
  id: 'test-1',
  db_id: 1,
  title: 'Test Book',
  content_type: 'book',
  source: 'goodreads',
  status: 'unread',
  rating: null,
  review: null,
  author: 'Author',
  seasons_watched: null,
  total_seasons: null,
  ignored: false,
  genres: [],
  tags: [],
  description: null,
  enrichment_status: null,
}

describe('EditModal', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('has role="dialog" and aria-modal', () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false },
      attachTo: document.body,
    })
    const dialog = wrapper.find('[role="dialog"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.attributes('aria-modal')).toBe('true')
    wrapper.unmount()
  })

  it('has aria-labelledby matching the title heading', () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false },
      attachTo: document.body,
    })
    const dialog = wrapper.find('[role="dialog"]')
    const heading = wrapper.find('#edit-modal-title')
    expect(dialog.attributes('aria-labelledby')).toBe('edit-modal-title')
    expect(heading.text()).toBe('Test Book')
    wrapper.unmount()
  })

  it('form fields have label/id associations', () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false },
      attachTo: document.body,
    })
    expect(wrapper.find('label[for="edit-status"]').exists()).toBe(true)
    expect(wrapper.find('#edit-status').exists()).toBe(true)
    expect(wrapper.find('label[for="edit-review"]').exists()).toBe(true)
    expect(wrapper.find('#edit-review').exists()).toBe(true)
    wrapper.unmount()
  })

  it('Escape key emits close', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false },
      attachTo: document.body,
    })
    await vi.runAllTimersAsync()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('backdrop click emits close', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false },
      attachTo: document.body,
    })
    await wrapper.find('.edit-modal').trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('save emits with correct payload', async () => {
    const wrapper = mount(EditModal, {
      props: { item: defaultItem, saving: false },
      attachTo: document.body,
    })
    await wrapper.find('#edit-status').setValue('completed')
    await wrapper.findAll('.btn-primary').find(b => b.text().includes('Save'))!.trigger('click')
    const emitted = wrapper.emitted('save')!
    expect(emitted[0][0]).toBe(1)
    expect(emitted[0][1]).toMatchObject({ status: 'completed' })
    wrapper.unmount()
  })
})
