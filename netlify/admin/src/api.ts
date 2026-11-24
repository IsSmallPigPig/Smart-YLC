export type CodeRecord = {
  code: string
  boundAt: string
  lastSeenAt: string
  relayAllowed: boolean
  backupUpdatedAt: string
  relayFiles: number
  relayBytes: number
}

export type DeviceRecord = {
  id: string
  name: string
  platform: string
  code: string
  firstSeenAt: string
  lastSeenAt: string
}

export type Settings = { relay: 'netlify' | 'r2'; r2Bucket: string; updatedAt: string }

export type Stats = {
  backups: number
  relayFiles: number
  relayBytes: number
  devices: number
  updatedAt: string
}

export type Session = {
  ok: boolean
  settings: Settings
  codes: CodeRecord[]
  devices: DeviceRecord[]
  stats: Stats
}

const TOKEN_KEY = 'smartyouth-admin-token'

export function token(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? ''
}

export function setToken(value: string): void {
  if (value) sessionStorage.setItem(TOKEN_KEY, value)
  else sessionStorage.removeItem(TOKEN_KEY)
}

async function call(action: string, init?: RequestInit): Promise<any> {
  const headers = new Headers(init?.headers)
  const current = token()
  if (current) headers.set('x-admin-token', current)
  const response = await fetch(`/api/admin?action=${action}`, { ...init, headers })
  const data = await response.json()
  if (!response.ok || data.ok === false) throw new Error(data.error ?? '请求失败')
  return data
}

export const api = {
  public: () => call('public') as Promise<Stats>,
  session: () => call('session') as Promise<Session>,
  relay: (settings: { provider: 'netlify' | 'r2'; r2Bucket: string }) =>
    call('relay', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(settings),
    }),
  code: (code: string, relayAllowed: boolean) =>
    call('code', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code, relayAllowed }),
    }),
  removeCode: (code: string) =>
    call(`code&id=${encodeURIComponent(code)}`, { method: 'DELETE' }),
}

export function time(value: string): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
