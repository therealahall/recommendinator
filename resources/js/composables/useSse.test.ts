import { describe, it, expect, vi } from 'vitest'
import { readSseStream } from './useSse'

function makeResponse(text: string): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

describe('readSseStream', () => {
  it('parses SSE data lines', async () => {
    const chunks: unknown[] = []
    const response = makeResponse(
      'data: {"type":"text","content":"hello"}\n' +
      'data: {"type":"done"}\n',
    )

    await readSseStream(response, (data) => chunks.push(data))

    expect(chunks).toEqual([
      { type: 'text', content: 'hello' },
      { type: 'done' },
    ])
  })

  it('ignores non-data lines', async () => {
    const chunks: unknown[] = []
    const response = makeResponse(
      ': comment\n' +
      'event: message\n' +
      'data: {"type":"text","content":"hi"}\n' +
      '\n',
    )

    await readSseStream(response, (data) => chunks.push(data))

    expect(chunks).toEqual([{ type: 'text', content: 'hi' }])
  })

  it('handles malformed JSON gracefully', async () => {
    const chunks: unknown[] = []
    const response = makeResponse(
      'data: not json\n' +
      'data: {"type":"ok"}\n',
    )

    await readSseStream(response, (data) => chunks.push(data))

    expect(chunks).toEqual([{ type: 'ok' }])
  })

  it('handles empty body fallback', async () => {
    const chunks: unknown[] = []
    // Create a response without readable stream body
    const response = new Response(
      'data: {"type":"text","content":"fallback"}\n',
      { status: 200 },
    )
    // Override body to null to test fallback
    Object.defineProperty(response, 'body', { value: null })

    await readSseStream(response, (data) => chunks.push(data))

    expect(chunks).toEqual([{ type: 'text', content: 'fallback' }])
  })
})
