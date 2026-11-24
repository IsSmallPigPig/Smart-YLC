# 智慧团委 · 云端服务

这个分支只放部署到 Netlify 的三个函数和两个页面。桌面端在 `main` 分支，手机端在 `mobile-v2` 分支，
两边都通过网络调这里的接口。

`backup` 存加密后的配置信封（服务端看不到明文）；`archive` 是手机连不上局域网时的中转档案，
按 3MB 分片收发，避开函数 6MB 请求/响应墙；`admin` 给后台和公开统计用。都以 6 位提取码为键，
不需要数据库。

| 组成 | 位置 | 说明 |
|---|---|---|
| `backup` 函数 | `netlify/functions/backup.mts` | 按提取码存取加密信封，单份 5MB |
| `archive` 函数 | `netlify/functions/archive.mts` | 中转档案，单片 3MB |
| `admin` 函数 | `netlify/functions/admin.mts` | 后台接口与公开统计，`ADMIN_TOKEN` 鉴权 |
| 页面 | `netlify/admin/` → 产物 `netlify/public/` | `/` 公开数据，`/admin` 管理页 |
| 存储 | Netlify Blobs | `smartyouth-backup` 信封、`smartyouth-archive` 索引与分片、`smartyouth-admin` 提取码/设备/中转设置 |

## 部署

### 第一次部署

1. 在 Netlify 建一个空站点，**不要连 GitHub**。发布和函数都靠 CI 用 CLI 直接上传，
   `netlify.toml` 只声明发布目录 `netlify/public` 与函数目录 `netlify/functions`。
   连上仓库并开启自动构建会有反效果：`netlify/public` 在 `.gitignore` 里，Netlify 自己跑构建
   只会发布一个空目录，把正常版本覆盖掉。
2. 给仓库加两个 secret，CI 用它部署：

   | Secret | 取处 |
   |---|---|
   | `NETLIFY_AUTH_TOKEN` | Netlify → User settings → Applications → Personal access tokens |
   | `NETLIFY_SITE_ID` | 站点 → Project configuration → General → Project information → Project ID |

   缺这两个时工作流只做打包校验、跳过部署，日志里有 warning。
3. 配环境变量，见下一节。函数读的是部署时快照，**改完要重新部署一次才生效**。

### 环境变量

| 变量 | 必需 | 用途 |
|---|---|---|
| `ADMIN_TOKEN` | 是 | 后台登录口令。没配时管理接口一律 401，公开统计正常 |
| `R2_ACCOUNT_ID` | 选 | 用 Cloudflare R2 存中转分片时必需 |
| `R2_ACCESS_KEY_ID` | 选 | 同上 |
| `R2_SECRET_ACCESS_KEY` | 选 | 同上 |
| `R2_BUCKET` | 选 | 桶名的兜底值；后台「中转存储」里填了就以后台为准 |

```sh
netlify link --id <SITE_ID>                 # 只需一次，写 .netlify/state.json（已在 .gitignore）
netlify env:set ADMIN_TOKEN "<口令>" --context production
netlify env:list --context production       # 核对
```

口令自己定，24 位以上，不含空格。R2 凭据在 Cloudflare 控制台 → R2 → Manage R2 API Tokens 建，
权限给 Object Read & Write，作用域限定到目标桶；桶不用新建，后台填已有桶名即可。
索引与提取码始终留在 Netlify，只有分片字节写到 R2。

### 日常发布

改 `netlify/` 下的文件，推到 `server`，`.github/workflows/cloud-backup.yml` 会：

1. `npm install`，用 esbuild 把三个函数各打一遍包（只校验，不产出）；
2. 在 `netlify/admin/` 里 `npm install && npm run build`，产物到 `netlify/public/`；
3. 检查产物 CSS 里有没有 `--f-text-3:`——没有说明组件库样式又漏了，直接失败；
4. `netlify-cli deploy --prod --dir=netlify/public --functions=netlify/functions`。

工作流只在 `server` 分支上。GitHub 页面的「Run workflow」按钮读的是默认分支的定义，所以那里看不到它；
要手动重跑就打开旧运行，点 `··· → Re-run all jobs`。

`@netlify/blobs` 由 Netlify 运行时提供，不需要配置；`netlify/package.json` 里的依赖在部署时自动安装。

### 本地调试

```sh
cd netlify/admin
npm install
npm run dev          # http://localhost:5173，/api 反代到 https://smartylc.netlify.app
```

`vite.config.ts` 里的 `server.proxy` 指向线上站点，本地页面调的是真实接口：公开数据是真的，
后台要输口令，改动会写线上存储。**别在这上面点「移除」**——那是真删。

只想连函数：`netlify dev` 会同时起函数和静态目录，需要本机登录 Netlify CLI。

### 不要用本机 `netlify deploy`

```sh
netlify deploy --prod --dir=netlify/public --functions=netlify/functions   # 卡在扩展安装
netlify deploy --prod --no-build ...                                       # 函数没打包，线上 502
```

站点挂了 Neon 扩展，本机跑构建时 `@netlify/sdk--ui-react` 装不上，构建直接失败；加 `--no-build`
能上传，但函数是以未打包的 `.mts` 上传的，调用返回 `invalid status code returned from lambda: 0`。
发布只走 CI。

### 部署后验收

```sh
curl -s https://<站点>/api/admin?action=public                 # 200 + {backups, relayFiles, relayBytes, devices}
curl -s -o /dev/null -w '%{http_code}\n' https://<站点>/api/admin?action=session   # 401
curl -s -H 'x-admin-token: <口令>' https://<站点>/api/admin?action=session         # 200 + settings/codes/devices
curl -s -o /dev/null -w '%{http_code}\n' https://<站点>/admin.html                # 200
```

页面侧：`/` 四张数字卡有值；`/admin` 输口令后能看到提取码行、同步设备、中转存储当前项。

要试删除链路又不想动真实数据，先造一个一次性提取码再删掉它：

```sh
OWNER=$(python3 -c "print('a'*64)")
curl -s -X PUT "https://<站点>/api/backup?id=ZT9Z9Z" -H "x-owner-key: $OWNER" \
  -H 'content-type: application/json' -d '{"salt":"x","nonce":"y","ciphertext":"z"}'
curl -s -X DELETE -H 'x-admin-token: <口令>' "https://<站点>/api/admin?action=code&id=ZT9Z9Z"
curl -s -o /dev/null -w '%{http_code}\n' "https://<站点>/api/backup?id=ZT9Z9Z"   # 404
```

### 回滚

```sh
netlify api listSiteDeploys --data '{"site_id":"<SITE_ID>","per_page":5}'
netlify api restoreSiteDeploy --data '{"site_id":"<SITE_ID>","deploy_id":"<部署 ID>"}'
```

函数和静态文件一起回到那次部署，Blob 里的数据不动。回滚代码就 revert 提交再推 `server`。

## 接口（v2）

| 方法 | 路径 | 说明 |
|---|---|---|
| PUT/POST | `/api/backup?id=ABC234` | 存一份信封：`{v, salt, nonce, ciphertext}`，头带 `X-Owner-Key` |
| GET | `/api/backup?id=ABC234` | 取回信封（不含 owner 字段）；不存在返回 404 |
| GET | `/api/backup?id=ABC234&probe=1` | 建档查重：只回答 `{exists: true/false}` |

所有权：建档时服务端记录 `SHA-256(owner key)`。之后的 PUT 必须带匹配的
`X-Owner-Key`，否则 403——别人的提取码只能拉取，不能被覆盖。没有 owner 头
的 PUT 一律 403。v1 老记录没有 owner，只能被读取，不能再被改写。

约束：提取码 6 位，字符集 `A-Z2-9`（去掉 0/1/I/O）；owner key 为 64 位 hex；请求体上限 5MB；单 IP 每分钟 30 次。

## 中转档案接口（archive v1）

手机与电脑不在同一网络时，手机把 PDF 分片传到这里，电脑再拉回。完整契约见仓库
`docs/app-pair-protocol.md` 第 9 节（`main` 分支）。

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/archive?action=list&id=ABC234` | 只需 code | 待收文件元数据（name/size/sha256/parts） |
| PUT | `/api/archive?action=init&id=ABC234` | X-Owner-Key | 声明文件：`{id,name,size,sha256,parts}` |
| PUT | `/api/archive?action=part&id=ABC234&file=f1&part=0` | X-Owner-Key | 传一片（≤3MB 原始字节） |
| PUT | `/api/archive?action=finish&id=ABC234&file=f1` | X-Owner-Key | 合并校验 sha256，通过才标记可读 |
| GET | `/api/archive?action=pull&id=ABC234&file=f1&part=0` | 只需 code | 取一片 |
| DELETE | `/api/archive?action=clear&id=ABC234&file=f1` | X-Owner-Key | 删云端副本（幂等） |

读只需提取码——它本就是配对时双方核对过的分享码；写/删必须带 owner key。
每片 3MB，单份上限 200MB / 100 片；索引最多 500 个文件，满了要先让电脑接收清理。
`finish` 校验失败不会标记可读，损坏文件不会被电脑拉走。单 IP 每分钟 120 次（分片多，限流放宽）。

## 云端管理后台

站点根路径 `/` 展示不敏感的使用数据：备份份数、同步设备数、中转文件数与中转流量。
接口 `GET /api/admin?action=public` 返回同一组汇总数字，不含提取码、设备名或 IP。

后台在 `/admin`（也接受 `/admin.html`）。管理接口用站点环境变量 `ADMIN_TOKEN`
鉴权，页面把它放在 `x-admin-token` 头里。

| 动作 | 方法 | 说明 |
|---|---|---|
| `/api/admin?action=session` | GET | 提取码、同步设备、中转存储设置与汇总数字 |
| `/api/admin?action=relay` | POST | `{provider:"netlify"|"r2", r2Bucket:"..."}` |
| `/api/admin?action=code` | POST | `{code, relayAllowed}` |
| `/api/admin?action=code&id=CODE` | DELETE | 删除该提取码的备份与中转数据，见下 |

提取码在首次上传备份时自动登记，默认允许中转。后台关掉「允许中转」后，该提取码的中转接口返回 403，
备份接口不受影响。列表按存储里的实际备份生成，早于登记逻辑的老备份也会列出来，开关照实显示是否允许。

「移除」删除该提取码的备份信封、中转索引、全部分片和登记记录，删完不可恢复，页面因此有确认框。
切换中转存储只影响之后上传的分片，已上传的按原位置读取；切换前上传、还没被接收的分片，
回切到原存储才能拉到。

后台页面是 `netlify/admin/` 下的 React + TypeScript 应用，组件来自
[react-fluent](https://github.com/zhouchentao666/react-fluent)，按 commit 锁在 `package.json` 里。
它的样式表必须在 `main.tsx` 与 `admin.tsx` 显式 `import 'react-fluent/style.css'`，
CI 第 3 步会拦漏掉的情况。

## 存储与数据

| 键 | 存储 | 内容 |
|---|---|---|
| `<提取码>` | `smartyouth-backup` | `{v, salt, nonce, ciphertext, owner, updatedAt}` |
| `archive:<提取码>` | `smartyouth-archive` | 中转索引：文件列表、大小、sha256、分片数、owner |
| `archive:<提取码>:<文件>:<序号>` | `smartyouth-archive` 或 R2 | 分片字节 |
| `state` | `smartyouth-admin` | 提取码登记、设备、中转设置 |

## 已知约束

- 限流计数在函数实例内存里，多实例时只是第一道闸，挡的是脚本枚举，不是精确配额。
- 后台的设备列表只登记带设备头，或用所有权凭据上传过备份的设备；纯读操作不留痕。
- `react-fluent` 升级时改 `netlify/admin/package.json` 里的 commit 再跑一次 `npm install` 更新 lock。

## 其他平台

存储被收在三个函数里，换平台只需替换 `getStore` 那一行：Vercel 用 `@vercel/kv`，Cloudflare 用 KV 绑定，Supabase 用两张表。加密与接口形状都不用动。

## 为什么 6 位码够用

提取码同时是解密口令。服务端只存密文，猜中码也只是拿到一堆密文；真正的门槛在客户端 `PBKDF2-SHA256`（20 万次）——6 位码熵不高，靠迭代次数和限流把在线爆破成本拉高。若要更强，可在设置里允许「自定义长口令」，协议不变。
