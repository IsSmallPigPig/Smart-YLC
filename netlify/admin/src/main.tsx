import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Card, applyThemeColor } from 'react-fluent'
import { api, size, type Stats } from './api'
import { Group, Row, Shell } from './shell'
import 'react-fluent/style.css'
import './style.css'

applyThemeColor('#0078d4')

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card className="stat">
      <div className="stat__label">{label}</div>
      <div className="stat__value">{value}</div>
    </Card>
  )
}

function PublicPage() {
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    api.public().then(setStats).catch(() => setStats(null))
  }, [])

  return (
    <Shell title="智慧团委云端服务" caption="加密备份与中转同步的运行情况">
      <div className="stats">
        <Stat label="云端备份" value={stats ? String(stats.backups) : '—'} />
        <Stat label="同步设备" value={stats ? String(stats.devices) : '—'} />
        <Stat label="中转文件" value={stats ? String(stats.relayFiles) : '—'} />
        <Stat label="中转流量" value={stats ? size(stats.relayBytes) : '—'} />
      </div>
      <Group title="连接本服务">
        <Row
          title="桌面端"
          content="系统配置 → 云端备份，服务地址填 https://smartylc.netlify.app，再填提取码与密码。"
        />
        <Row
          title="内容加密"
          content="配置在设备上加密后上传，服务端只保存密文。"
        />
      </Group>
    </Shell>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode><PublicPage /></StrictMode>,
)
