import { describe, it, expect, vi } from 'vitest'
import { useSubmission } from './useSubmission'

describe('useSubmission', () => {
  it('starts with nothing to report', () => {
    const submission = useSubmission()

    expect(submission.pending).toBe(false)
    expect(submission.error).toBe('')
    expect(submission.saved).toBe(false)
  })

  it('is pending for the whole round trip, and saved once it works', async () => {
    let finish: (message: string) => void = () => {}
    const submission = useSubmission()

    const running = submission.submit(() => new Promise<string>((resolve) => (finish = resolve)))
    expect(submission.pending).toBe(true)
    expect(submission.saved).toBe(false)

    finish('')
    await running

    expect(submission.pending).toBe(false)
    expect(submission.error).toBe('')
    expect(submission.saved).toBe(true)
  })

  it('holds the message the action answered with, and calls that unsaved', async () => {
    const submission = useSubmission()

    await submission.submit(async () => 'That username is taken.')

    expect(submission.error).toBe('That username is taken.')
    expect(submission.saved).toBe(false)
    expect(submission.pending).toBe(false)
  })

  it('clears the previous outcome before the next attempt', async () => {
    const submission = useSubmission()
    await submission.submit(async () => 'That username is taken.')

    const running = submission.submit(async () => '')
    expect(submission.error).toBe('')

    await running
    expect(submission.saved).toBe(true)
  })

  it('ignores a second submission while one is in flight', async () => {
    const submission = useSubmission()
    const action = vi.fn(() => new Promise<string>((resolve) => setTimeout(() => resolve(''), 0)))

    const first = submission.submit(action)
    await submission.submit(action)
    await first

    expect(action).toHaveBeenCalledTimes(1)
  })

  it('stops being pending when the action throws', async () => {
    const submission = useSubmission()

    await expect(
      submission.submit(() => Promise.reject(new Error('boom'))),
    ).rejects.toThrow('boom')

    expect(submission.pending).toBe(false)
  })
})
