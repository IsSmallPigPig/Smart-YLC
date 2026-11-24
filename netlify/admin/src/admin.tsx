import { StrictMode, useCallback, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Card, LineEdit, PasswordLineEdit, PrimaryPushButton, PushButton,
  RadioButton, SwitchButton, applyThemeColor,
} from 'react-fluent'
import { api, setToken, size, time, token, type Session } from './api'
import './style.css'

applyThemeColor('#0078d4')

function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function enter() {
    setBusy(true)
    setError('')
    setToken(password.trim())
    try {
      await api.session()
      onDone()
    } catch (e) {
      setToken('')
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="page login">
      <h1>云端管理</h1>
      <PasswordLineEdit
        width={320}
        placeholder="管理口令"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && enter()}
      />
      <div className="inline">
        <PrimaryPushButton disabled={busy || !password.trim()} onClick={enter}>进入</PrimaryPushButton>
      </div>
      {error && <p className="error">{error}</p>}
    </main>
  )
}

function Codes({ session, reload }: { session: Session; reload: () => void }) {
  const [busy, setBusy] = useState('')

  async function toggle(code: string, allowed: boolean) {
    setBusy(code)
    try {
      await api.code(code, allowed)
      reload()
    } finally {
      setBusy('')
    }
  }

  async function remove(code: string) {
    setBusy(code)
    try {
      await api.removeCode(code)
      reload()
    } finally {
      setBusy('')
    }
  }

  return (
    <Card>
      <div className="card-head">
        <h2>提取码</h2>
        <span className="hint">{session.codes.length} 个</span>
      </div>
      {session.codes.length === 0 ? <div className="empty">还没有设备连接</div> : (
        <table className="table">
          <thead><tr><th>提取码</th><th>最近活动</th><th>备份</th><th>中转文件</th><th>允许中转</th><th /></tr></thead>
          <tbody>
            {session.codes.map((c) => (
              <tr key={c.code}>
                <td className="mono">{c.code}</td>
                <td>{time(c.lastSeenAt)}</td>
                <td>{c.backupUpdatedAt ? time(c.backupUpdatedAt) : '—'}</td>
                <td>{c.relayFiles} · {size(c.relayBytes)}</td>
                <td>
                  <SwitchButton
                    key={`${c.code}-${c.relayAllowed}`}
                    defaultChecked={c.relayAllowed}
                    disabled={busy === c.code}
                    onChange={(next) => toggle(c.code, next)}
                  />
                </td>
                <td>
                  <div className="row-actions">
                    <PushButton disabled={busy === c.code} onClick={() => remove(c.code)}>移除</PushButton>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

function Devices({ session }: { session: Session }) {
  return (
    <Card>
      <div className="card-head">
        <h2>同步设备</h2>
        <span className="hint">{session.devices.length} 台</span>
      </div>
      {session.devices.length === 0 ? <div className="empty">还没有中转记录</div> : (
        <table className="table">
          <thead><tr><th>设备</th><th>类型</th><th>提取码</th><th>最近活动</th></tr></thead>
          <tbody>
            {session.devices.map((d) => (
              <tr key={d.id}>
                <td>{d.name}</td>
                <td>{d.platform}</td>
                <td className="mono">{d.code}</td>
                <td>{time(d.lastSeenAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

function Relay({ session, reload }: { session: Session; reload: () => void }) {
  const [provider, setProvider] = useState<'netlify' | 'r2'>(session.settings.relay)
  const [bucket, setBucket] = useState(session.settings.r2Bucket)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  async function save() {
    setError('')
    setSaved(false)
    try {
      await api.relay({ provider, r2Bucket: bucket.trim() })
      setSaved(true)
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <Card>
      <div className="card-head"><h2>中转存储</h2></div>
      <div className="stack">
        <div className="inline">
          <RadioButton text="Netlify" checked={provider === 'netlify'} onChange={() => setProvider('netlify')} />
          <RadioButton text="Cloudflare R2" checked={provider === 'r2'} onChange={() => setProvider('r2')} />
        </div>
        {provider === 'r2' && (
          <div className="inline">
            <LineEdit
              width={320}
              placeholder="存储桶名称"
              value={bucket}
              onChange={(e) => setBucket(e.target.value)}
            />
          </div>
        )}
        <div className="inline">
          <PrimaryPushButton onClick={save}>保存</PrimaryPushButton>
          {saved && <span className="hint">已保存</span>}
        </div>
        {error && <p className="error">{error}</p>}
      </div>
    </Card>
  )
}

function Dashboard() {
  const [session, setSession] = useState<Session | null>(null)
  const [error, setError] = useState('')

  const reload = useCallback(() => {
    api.session().then((data) => {
      setSession(data)
      setError('')
    }).catch((e: Error) => {
      setError(e.message)
      if (e.message === '需要管理口令') {
        setToken('')
        window.location.reload()
      }
    })
  }, [])

  useEffect(reload, [reload])

  if (!session) return <main className="page"><p className="hint">{error || '载入中'}</p></main>

  return (
    <main className="page">
      <div className="card-head">
        <h1>云端管理</h1>
        <PushButton onClick={reload}>刷新</PushButton>
      </div>
      <div className="stack">
        <Codes session={session} reload={reload} />
        <Devices session={session} />
        <Relay session={session} reload={reload} />
      </div>
    </main>
  )
}

function App() {
  const [authorized, setAuthorized] = useState(Boolean(token()))
  return authorized ? <Dashboard /> : <Login onDone={() => setAuthorized(true)} />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><App /></StrictMode>,
)
