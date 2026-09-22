export function parseUniqueJson(text: string): unknown {
  let cursor = 0
  function whitespace() {
    while (cursor < text.length && /[\t\n\r ]/.test(text[cursor])) cursor++
  }
  function fail(message: string): never {
    throw new Error(`${message} near character ${cursor + 1}.`)
  }
  function string(): string {
    const start = cursor++
    while (cursor < text.length) {
      const char = text[cursor++]
      if (char === '\\') cursor++
      else if (char === '"') {
        const value: unknown = JSON.parse(text.slice(start, cursor))
        if (typeof value !== 'string') return fail('Expected a JSON string')
        return value
      }
    }
    return fail('Unterminated JSON string')
  }
  function value(depth: number): unknown {
    if (depth > 20) return fail('JSON is nested too deeply')
    whitespace()
    const char = text[cursor]
    if (char === '"') return string()
    if (char === '{') {
      cursor++
      const result: Record<string, unknown> = Object.create(null)
      whitespace()
      if (text[cursor] === '}') {
        cursor++
        return result
      }
      while (cursor < text.length) {
        whitespace()
        if (text[cursor] !== '"') return fail('Expected a property name')
        const key = string()
        if (Object.hasOwn(result, key)) return fail(`Duplicate JSON property "${key}"`)
        whitespace()
        if (text[cursor++] !== ':') return fail('Expected a colon')
        result[key] = value(depth + 1)
        whitespace()
        const next = text[cursor++]
        if (next === '}') return result
        if (next !== ',') return fail('Expected a comma or closing brace')
      }
      return fail('Unterminated JSON object')
    }
    if (char === '[') {
      cursor++
      const result: unknown[] = []
      whitespace()
      if (text[cursor] === ']') {
        cursor++
        return result
      }
      while (cursor < text.length) {
        result.push(value(depth + 1))
        whitespace()
        const next = text[cursor++]
        if (next === ']') return result
        if (next !== ',') return fail('Expected a comma or closing bracket')
      }
      return fail('Unterminated JSON array')
    }
    for (const [literal, result] of [
      ['true', true],
      ['false', false],
      ['null', null],
    ] as const) {
      if (text.startsWith(literal, cursor)) {
        cursor += literal.length
        return result
      }
    }
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(cursor))
    if (!match) return fail('Expected valid JSON')
    cursor += match[0].length
    const number = Number(match[0])
    if (!Number.isFinite(number)) return fail('Numbers must be finite')
    return number
  }
  const result = value(0)
  whitespace()
  if (cursor !== text.length) fail('Unexpected text after JSON')
  return result
}
