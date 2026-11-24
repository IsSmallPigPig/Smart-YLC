import { StrictMode, useCallback, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  LineEdit, MessageBox, PasswordLineEdit, PrimaryPushButton, PushButton,
  RadioButton, SwitchButton, applyThemeColor,
} from 'react-fluent'
import { api, setToken, size, time, token, type Session } from './api'
import { Group, Row, Shell } from './shell'
import 'react-fluent/style.css'
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
    <Shell title="云端管理" caption="管理提取码、同步设备与中转存储">
      <Group title="管理口令">
        <div className="setcard setcard--form">
          <div className="setcard__text">
            <div className="row-title">口令</div>
            <div className="setcard__content">站点环境变量里的 ADMIN_TOKEN</div>
          </div>
          <div className="setcard__control row-control">
            <PasswordLineEdit
              width={240}
              placeholder="管理口令"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && enter()}
            />
            <PrimaryPushButton disabled={busy || !password.trim()} onClick={enter}>进入</PrimaryPushButton>
          </div>
        </div>
      </Group>
      {error && <p className="form-error">{error}</p>}
    </Shell>
  )
}

function Codes({ session, reload }: { session: Session; reload: () => void }) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState<string | null>(null)
  const target = session.codes.find((c) => c.code === confirming)

  async function run(code: string, action: () => Promise<unknown>) {
    setBusy(code)
    setError('')
    try {
      await action()
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  return (
    <>
      <Group
        title="提取码"
        aside={session.codes.length > 0 ? `${session.codes.length} 个` : undefined}
      >
        {session.codes.length === 0
          ? <Row content="还没有提取码。桌面端上传一次备份后，这里会出现它的提取码。" />
          : session.codes.map((c) => (
            <Row
              key={c.code}
              title={c.code}
              mono
              content={`最近活动 ${time(c.lastSeenAt)}，备份 ${time(c.backupUpdatedAt)}，中转 ${c.relayFiles} 个文件 ${size(c.relayBytes)}`}
              control={<>
                <SwitchButton
                  key={`${c.code}-${c.relayAllowed}`}
                  defaultChecked={c.relayAllowed}
                  disabled={busy === c.code}
                  onText="允许中转"
                  offText="不允许中转"
                  onChange={(next) => run(c.code, () => api.code(c.code, next))}
                />
                <PushButton disabled={busy === c.code} onClick={() => setConfirming(c.code)}>移除</PushButton>
              </>}
            />
          ))}
      </Group>
      <MessageBox
        open={confirming !== null}
        title={`移除 ${confirming ?? ''}？`}
        content={target
          ? `云端备份和 ${target.relayFiles} 个中转文件会被删除，这份配置之后无法再从云端恢复。`
          : ''}
        yesText="移除"
        onYes={() => {
          const code = confirming
          setConfirming(null)
          if (code) run(code, () => api.removeCode(code))
        }}
        onCancel={() => setConfirming(null)}
      />
      {error && <p className="form-error">{error}</p>}
    </>
  )
}

function Devices({ session }: { session: Session }) {
  return (
    <Group
      title="同步设备"
      aside={session.devices.length > 0 ? `${session.devices.length} 台` : undefined}
    >
      {session.devices.length === 0
        ? <Row content="还没有设备连接。桌面端上传备份或走中转后，这里会出现对应的设备。" />
        : session.devices.map((d) => (
          <Row
            key={d.id}
            title={`${d.name}（${d.platform}）`}
            content={`提取码 ${d.code}，最近活动 ${time(d.lastSeenAt)}`}
          />
        ))}
    </Group>
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

  const current = session.settings.relay === 'r2'
    ? `Cloudflare R2，${session.settings.r2Bucket || '未填桶名'}`
    : 'Netlify'

  return (
    <>
      <Group title="中转存储" aside={`当前 ${current}`}>
        <Row
          title="文件存放位置"
          content="设备之间传的文件中转到这里"
          control={<>
            <RadioButton text="Netlify" checked={provider === 'netlify'} onChange={() => setProvider('netlify')} />
            <RadioButton text="Cloudflare R2" checked={provider === 'r2'} onChange={() => setProvider('r2')} />
          </>}
        />
        {provider === 'r2' && (
          <Row
            title="存储桶名称"
            content="填 Cloudflare 上已有的桶"
            control={
              <LineEdit
                width={240}
                placeholder="存储桶名称"
                value={bucket}
                onChange={(e) => setBucket(e.target.value)}
              />
            }
          />
        )}
        <Row
          content={saved ? '已保存，之后上传的中转文件走新位置' : '保存后只影响之后上传的中转文件'}
          control={<PrimaryPushButton onClick={save}>保存</PrimaryPushButton>}
        />
      </Group>
      {error && <p className="form-error">{error}</p>}
    </>
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

  return (
    <Shell
      title="云端管理"
      caption="提取码、同步设备与中转存储"
      actions={<PushButton onClick={reload}>刷新</PushButton>}
    >
      {session ? (
        <>
          <Codes session={session} reload={reload} />
          <Devices session={session} />
          <Relay session={session} reload={reload} />
        </>
      ) : (
        <Group title="状态">
          <Row content={error || '正在读取云端状态'} />
        </Group>
      )}
    </Shell>
  )
}

function App() {
  const [authorized, setAuthorized] = useState(Boolean(token()))
  return authorized ? <Dashboard /> : <Login onDone={() => setAuthorized(true)} />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><App /></StrictMode>,
)
