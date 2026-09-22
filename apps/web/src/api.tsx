import { createContext, useContext } from 'react'
import type { Config } from './types'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail: unknown,
  ) {
    super(message)
  }
}

export function createApi(token: () => Promise<string>) {
  async function response(path: string, options: RequestInit = {}) {
    const headers = new Headers(options.headers)
    headers.set('Authorization', `Bearer ${await token()}`)
    if (options.body && !(options.body instanceof FormData))
      headers.set('Content-Type', 'application/json')
    const result = await fetch(`/api${path}`, { ...options, headers, credentials: 'omit' })
    if (!result.ok) {
      const text = await result.text()
      let detail: unknown = text
      try {
        detail = JSON.parse(text).detail
      } catch {
        /* Non-JSON service errors keep their body. */
      }
      let message = typeof detail === 'string' ? detail : JSON.stringify(detail)
      if (typeof detail === 'object' && detail !== null && 'message' in detail)
        message = String(detail.message)
      throw new ApiError(result.status, message || `Request failed (${result.status})`, detail)
    }
    return result
  }
  return {
    async get<T>(path: string): Promise<T> {
      return (await response(path)).json()
    },
    async send<T>(path: string, method: string, body?: unknown, version?: number): Promise<T> {
      const result = await response(path, {
        method,
        body:
          body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
        headers: version === undefined ? {} : { 'If-Match': `"${version}"` },
      })
      return result.json()
    },
    async remove(path: string): Promise<void> {
      await response(path, { method: 'DELETE' })
    },
    async blob(path: string) {
      return (await response(path)).blob()
    },
  }
}
export type Api = ReturnType<typeof createApi>
export const SessionContext = createContext<{ api: Api; config: Config } | null>(null)
export function useSession() {
  const context = useContext(SessionContext)
  if (!context) throw new Error('Authenticated session is required')
  return context
}

export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <div className="notice error" role="alert">
      {error instanceof Error ? error.message : String(error)}
    </div>
  )
}

export function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
