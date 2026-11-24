# 云端服务部署

这个分支只有 Netlify 上跑的东西：三个函数、三块 Blob 存储、一个静态前端。桌面端在 `main`，
手机端在 `mobile-v2`，它们都通过网络调这里。

| 组成 | 位置 | 说明 |
|---|---|---|
| `backup` 函数 | `netlify/functions/backup.mts` | 按提取码存取一份加密配置信封，5MB 上限 |
| `archive` 函数 | `netlify/functions/archive.mts` | 手机到电脑的中转档案，3MB 一片 |
| `admin` 函数 | `netlify/functions/admin.mts` | 后台接口 + 公开统计，`ADMIN_TOKEN` 鉴权 |
| 前端 | `netlify/admin/` → 产物 `netlify/public/` | `/` 公开数据，`/admin` 管理页 |
| 存储 | Netlify Blobs | `smartyouth-backup`（信封）、`smartyouth-archive`（索引与分片）、`smartyouth-admin`（提取码、设备、中转设置） |

## 第一次部署

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

3. 配环境变量（见下一节）。函数读的是部署时快照，**改完要重新部署一次才生效**。

## 环境变量

| 变量 | 必需 | 用途 |
|---|---|---|
| `ADMIN_TOKEN` | 是 | 后台登录口令。没配时 `/api/admin` 的管理动作一律 401，公开统计正常 |
| `R2_ACCOUNT_ID` | 选 | 用 Cloudflare R2 存中转分片时必需 |
| `R2_ACCESS_KEY_ID` | 选 | 同上 |
| `R2_SECRET_ACCESS_KEY` | 选 | 同上 |
| `R2_BUCKET` | 选 | 桶名的兜底值；后台「中转存储」里填了就以后台为准 |

```sh
netlify link --id <SITE_ID>                 # 只需一次，写 .netlify/state.json（已在 .gitignore）
netlify env:set ADMIN_TOKEN "<口令>" --context production
netlify env:list --context production       # 核对
```

口令自己定，长度 24 位以上，别用中文或空格。改完从 CI 重新部署一次，`Authorization` 才认新值。

R2 的凭据在 Cloudflare 控制台 → R2 → Manage R2 API Tokens 建，权限给 Object Read & Write，
作用域限定到目标桶。桶本身不用新建，后台填已有桶名即可；索引与提取码始终留在 Netlify，
只有分片字节写到 R2。

## 日常发布

改 `netlify/` 下的文件，推到 `server`，`.github/workflows/cloud-backup.yml` 会：

1. `npm install`，用 esbuild 把三个函数各打一遍包（只校验，不产出）；
2. 在 `netlify/admin/` 里 `npm install && npm run build`，产物到 `netlify/public/`；
3. 检查产物 CSS 里有没有 `--f-text-3:`——没有说明组件库样式又漏了，直接失败；
4. `netlify-cli deploy --prod --dir=netlify/public --functions=netlify/functions`。

工作流只在 `server` 分支上。GitHub 页面的「Run workflow」按钮读的是默认分支的定义，
所以那里看不到它；要手动重跑就打开旧运行，点 `··· → Re-run all jobs`。

## 本地调试

```sh
cd netlify/admin
npm install
npm run dev          # http://localhost:5173，/api 反代到 https://smartylc.netlify.app
```

`vite.config.ts` 里的 `server.proxy` 指向线上站点，所以本地页面调的是真实接口：公开数据是真的，
后台要输口令、改动会写线上存储。**别在这上面点「移除」**——那是真删。

只想看函数：`netlify dev` 会同时起函数和前端，需要本机登录 Netlify CLI。

## 不要用本机 `netlify deploy`

```sh
netlify deploy --prod --dir=netlify/public --functions=netlify/functions   # 会卡在扩展安装
netlify deploy --prod --no-build ...                                       # 函数没打包，线上 502
```

这个站点挂了 Neon 扩展，本机跑构建时 `@netlify/sdk--ui-react` 装不上，构建直接失败；
加 `--no-build` 能上传，但函数是以未打包的 `.mts` 上传的，调用返回
`invalid status code returned from lambda: 0`。发布只走 CI。

## 部署后验收

```sh
curl -s https://<站点>/api/admin?action=public          # 200 + {backups, relayFiles, relayBytes, devices}
curl -s -o /dev/null -w '%{http_code}\n' https://<站点>/api/admin?action=session          # 401
curl -s -H 'x-admin-token: <口令>' https://<站点>/api/admin?action=session                # 200 + settings/codes/devices
curl -s -o /dev/null -w '%{http_code}\n' https://<站点>/admin.html                       # 200
```

页面侧：`/` 四张数字卡有值；`/admin` 输口令后能看到提取码行、同步设备、中转存储当前项。

要试删除链路又不想动真实数据，先造一个一次性提取码，再删掉它：

```sh
OWNER=$(python3 -c "print('a'*64)")
curl -s -X PUT "https://<站点>/api/backup?id=ZT9Z9Z" -H "x-owner-key: $OWNER" \
  -H 'content-type: application/json' -d '{"salt":"x","nonce":"y","ciphertext":"z"}'
curl -s -X DELETE -H 'x-admin-token: <口令>' "https://<站点>/api/admin?action=code&id=ZT9Z9Z"
curl -s -o /dev/null -w '%{http_code}\n' "https://<站点>/api/backup?id=ZT9Z9Z"   # 404
```

## 回滚

```sh
netlify api listSiteDeploys --data '{"site_id":"<SITE_ID>","per_page":5}'
netlify api restoreSiteDeploy --data '{"site_id":"<SITE_ID>","deploy_id":"<部署 ID>"}'
```

函数和静态文件一起回到那次部署，Blob 里的数据不动。要回滚代码就 revert 提交再推 `server`。

## 数据与删除

| 键 | 存储 | 内容 |
|---|---|---|
| `<提取码>` | `smartyouth-backup` | `{v, salt, nonce, ciphertext, owner, updatedAt}` |
| `archive:<提取码>` | `smartyouth-archive` | 中转索引：文件列表、大小、sha256、分片数、owner |
| `archive:<提取码>:<文件>:<序号>` | `smartyouth-archive` 或 R2 | 分片字节 |
| `state` | `smartyouth-admin` | 提取码登记、设备、中转设置 |

后台「移除」会删掉这个提取码的备份信封、中转索引、全部分片和登记记录，删完不可恢复，
所以按钮前面有确认框。切换中转存储只影响之后上传的分片，已上传的按原位置读取；
若在切换前上传的分片还没被接收，回切到原存储才能拉到。

## 已知约束

- 提取码 6 位，字符集 `A-Z2-9`（不含 0/1/I/O）；owner key 为 64 位 hex。
- 备份单份 5MB，单 IP 每分钟 30 次；中转单片 3MB，单份 200MB / 100 片，单 IP 每分钟 120 次。
- 限流计数在函数实例内存里，多实例时只是第一道闸，挡的是脚本枚举，不是精确配额。
- `react-fluent` 按 commit 锁在 `netlify/admin/package.json`，升级时改那一行并跑一次
  `npm install` 更新 lock；它的样式表必须在两个入口显式 `import 'react-fluent/style.css'`，
  CI 的第 3 步会拦。
