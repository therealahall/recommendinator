/**
 * Generic SSE ReadableStream reader.
 * Parses "data: {...}" lines and invokes a callback for each parsed JSON chunk.
 */
export async function readSseStream<T>(
  response: Response,
  onChunk: (data: T) => void,
): Promise<void> {
  if (!response.body) {
    // Fallback: read entire response as text and parse lines
    const text = await response.text()
    for (const line of text.split('\n')) {
      processLine(line, onChunk)
    }
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      processLine(line, onChunk)
    }
  }

  // Process any remaining buffer
  if (buffer.trim()) {
    processLine(buffer, onChunk)
  }
}

function processLine<T>(line: string, onChunk: (data: T) => void): void {
  if (!line.startsWith('data: ')) return
  try {
    const data = JSON.parse(line.substring(6)) as T
    onChunk(data)
  } catch {
    // Skip malformed JSON
  }
}
