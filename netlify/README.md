# 云端备份服务

一个函数、一个键值存储：客户端把**加密后的信封**放到以提取码为键的位置，服务端看不到明文，也不需要数据库。

## 部署到 Netlify

仓库里已有 `.github/workflows/cloud-backup.yml`：推到 `server` 或 `main` 时先做打包校验，再部署。需要两个仓库 secret：

| Secret | 取处 |
|---|---|
| `NETLIFY_AUTH_TOKEN` | Netlify → User settings → Applications → Personal access tokens |
| `NETLIFY_SITE_ID` | 站点 → Project configuration → General → Project details → Project information → Project ID（旧界面叫 Site ID） |

没配这两个 secret 时工作流只跑校验、跳过部署（日志里有 warning），不会红。

这个 workflow 只在 `server` 分支上，GitHub 页面的「Run workflow」按钮读取默认分支的 workflow 定义，所以这里看不到它。改 `netlify/` 下的文件并推到 `server` 就会触发部署；GitHub Actions 页面点旧运行右侧的 `··· → Re-run all jobs` 也可以。

也可以让 Netlify 直接连仓库：仓库根的 `netlify.toml` 已声明发布目录 `netlify/public`、函数目录 `netlify/functions`，Netlify 会自动读到，不用手填构建参数。

`@netlify/blobs` 在 Netlify 运行时自带，环境变量不需要配置。部署后地址形如 `https://<站点>.netlify.app`，在客户端「设置 → 系统备份 → 云端备份」里填站点根地址即可。

函数按需安装依赖：Netlify 会自动读取 `netlify/package.json` 并在部署时安装 `@netlify/blobs`。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| PUT/POST | `/api/backup?id=ABC234` | 存一份信封：`{salt, nonce, ciphertext}` |
| GET | `/api/backup?id=ABC234` | 取回同一份信封；不存在返回 404 |

约束：提取码 6 位，字符集 `A-Z2-9`（去掉 0/1/I/O）；请求体上限 5MB；单 IP 每分钟 30 次。

## 其他平台

存储被收在一个函数里，换平台只需替换 `getStore` 那一行：Vercel 用 `@vercel/kv`，Cloudflare 用 KV 绑定，Supabase 用一张 `backups(code text primary key, envelope jsonb)` 表。加密与接口形状都不用动。

## 为什么 6 位码够用

提取码同时是解密口令。服务端只存密文，猜中码也只是拿到一堆密文；真正的门槛在客户端 `PBKDF2-SHA256`（20 万次）——6 位码熵不高，靠迭代次数和限流把在线爆破成本拉高。若要更强，可在设置里允许「自定义长口令」，协议不变。
