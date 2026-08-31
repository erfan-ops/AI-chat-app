/**
 * Minimal Server-Sent Events parser.
 *
 * The backend streams SSE over `fetch` (the message endpoint is a POST, so the
 * browser's EventSource API — which only supports GET — cannot be used).
 * The parser follows the SSE wire format: blocks separated by blank lines,
 * each block made of `event:` / `data:` lines (multi-line data joined by \n).
 */

export interface SseEvent {
  event: string
  data: string
}

function parseBlock(block: string): SseEvent | null {
  let event = 'message'
  const dataLines: string[] = []

  for (const rawLine of block.split('\n')) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (line === '' || line.startsWith(':')) continue // ignore keep-alive comments
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).replace(/^ /, ''))
    }
  }

  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseEvent> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let separatorIndex: number
      while ((separatorIndex = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, separatorIndex)
        buffer = buffer.slice(separatorIndex + 2)
        const event = parseBlock(block)
        if (event) yield event
      }
    }
    const rest = buffer.trim()
    if (rest !== '') {
      const event = parseBlock(rest)
      if (event) yield event
    }
  } finally {
    reader.releaseLock()
  }
}
