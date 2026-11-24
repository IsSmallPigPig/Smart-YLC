# 智慧团委 · 云端服务

这个分支只放部署到 Netlify 的两个函数。桌面端在 `main` 分支，手机端在 `mobile-v2` 分支，两边都通过网络调用这里的接口。

两个函数、一个键值存储：`backup` 存加密后的配置信封（服务端看不到明文）；
`archive` 为手机连不上局域网时的中转档案——按 3MB 分片收发，避开函数 6MB 请求/响应墙。
两端都以 6 位提取码为键，不需要数据库。

## 部署到 Netlify

`.github/workflows/cloud-backup.yml` 在推到本分支时先做打包校验，再部署。需要两个仓库 secret：

| Secret | 取处 |
|---|---|
| `NETLIFY_AUTH_TOKEN` | Netlify → User settings → Applications → Personal access tokens |
| `NETLIFY_SITE_ID` | 站点 → Project configuration → General → Project details → Project information → Project ID（旧界面叫 Site ID） |

没配这两个 secret 时工作流只跑校验、跳过部署（日志里有 warning），不会红。

这个 workflow 只在 `server` 分支上，GitHub 页面的「Run workflow」按钮读取默认分支的 workflow 定义，所以这里看不到它。改 `netlify/` 下的文件并推到 `server` 就会触发部署；GitHub Actions 页面点旧运行右侧的 `··· → Re-run all jobs` 也可以。

也可以让 Netlify 直接连仓库：仓库根的 `netlify.toml` 已声明发布目录 `netlify/public`、函数目录 `netlify/functions`，Netlify 会自动读到，不用手填构建参数。

`@netlify/blobs` 在 Netlify 运行时自带，环境变量不需要配置。部署后地址形如 `https://<站点>.netlify.app`，在客户端「设置 → 系统备份 → 云端备份」里填站点根地址即可。

函数按需安装依赖：Netlify 会自动读取 `netlify/package.json` 并在部署时安装 `@netlify/blobs`。

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

## 其他平台

存储被收在两个函数里，换平台只需替换 `getStore` 那一行：Vercel 用 `@vercel/kv`，Cloudflare 用 KV 绑定，Supabase 用两张表。加密与接口形状都不用动。

## 为什么 6 位码够用

提取码同时是解密口令。服务端只存密文，猜中码也只是拿到一堆密文；真正的门槛在客户端 `PBKDF2-SHA256`（20 万次）——6 位码熵不高，靠迭代次数和限流把在线爆破成本拉高。若要更强，可在设置里允许「自定义长口令」，协议不变。

## 云端管理后台

站点根路径 `/` 展示不敏感的使用数据：备份份数、同步设备数、中转文件数与中转流量。
接口 `GET /api/admin?action=public` 返回同一组汇总数字，不含提取码、设备名或 IP。

后台在 `/admin`（也接受 `/admin.html`）。管理接口用站点环境变量 `ADMIN_TOKEN`
鉴权，页面把它放在 `x-admin-token` 头里；没配这个变量时管理接口返回 401。

| 动作 | 方法 | 说明 |
|---|---|---|
| `/api/admin?action=session` | GET | 提取码、同步设备、中转存储设置与汇总数字 |
| `/api/admin?action=relay` | POST | `{provider:"netlify"|"r2", r2Bucket:"..."}` |
| `/api/admin?action=code` | POST | `{code, relayAllowed}` |
| `/api/admin?action=code&id=CODE` | DELETE | 移除记录及其设备 |

提取码首次备份时自动登记，默认允许中转。后台关掉「允许中转」后，该提取码的中转接口返回 403；
备份接口不受影响。移除记录同时清掉关联设备，下次使用时重新登记。

中转分片默认存在 Netlify Blobs。选 Cloudflare R2 时，在后台填存储桶名称，并把凭据配到站点环境变量：
`R2_ACCOUNT_ID`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`（可用 `R2_BUCKET` 代替后台填写）。
索引与提取码留在 Netlify，只有分片字节写到 R2；切换后新上传走新存储，已上传的按原位置读取。

后台页面是 `netlify/admin/` 下的 React + TypeScript 应用，组件来自
[react-fluent](https://github.com/zhouchentao666/react-fluent)。CI 在部署前执行
`npm install && npm run build`，产物输出到 `netlify/public/`，与函数一起部署。
