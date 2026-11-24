import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Card, applyThemeColor } from 'react-fluent'
import { api, size, type Stats } from './api'
import 'react-fluent/style.css'
import './style.css'

applyThemeColor('#0078d4')

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </Card>
  )
}

function PublicPage() {
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    api.public().then(setStats).catch(() => setStats(null))
  }, [])

  return (
    <main className="page">
      <h1>智慧团委云端服务</h1>
      <p className="lead">加密备份与中转同步的运行情况</p>
      <div className="grid">
        <Stat label="云端备份" value={stats ? String(stats.backups) : '—'} />
        <Stat label="同步设备" value={stats ? String(stats.devices) : '—'} />
        <Stat label="中转文件" value={stats ? String(stats.relayFiles) : '—'} />
        <Stat label="中转流量" value={stats ? size(stats.relayBytes) : '—'} />
      </div>
      <Card>
        <p className="hint">配置在设备上加密后上传，服务端只保存密文。应用内「系统配置 → 云端备份」可连接本服务。</p>
      </Card>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><PublicPage /></StrictMode>,
)
