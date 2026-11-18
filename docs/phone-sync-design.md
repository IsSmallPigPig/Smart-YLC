# 手机 → 电脑档案同步：Netlify 中转方案评估

## 结论先行

| 需求 | 可行性 | 依据 |
|---|---|---|
| Netlify 直传 10MB 以下 PDF | **不可行** | 函数请求体硬上限 6MB，二进制 base64 后实际约 4.5MB；参考档案最大 7.9MB |
| Netlify 分片中转 PDF | **可行** | Blobs 单对象上限 5GB，分片 PUT 再合并可绕开请求体限制 |
| 同步完删云端 | **可行** | 一次 DELETE 调用 |
| Netlify 做公网映射直连本机局域网 | **不可行** | Netlify 无反向隧道能力；需 Cloudflare Tunnel / frp 等，且电脑要常驻进程 |

推荐 **分片中转**，不引入额外隧道服务。

## 为什么直传不行

参考档案 49 份实测：单份 1.25–7.9MB，均值 3.3MB。
Netlify Functions 的请求/响应是 **buffered 6MB**，二进制走 base64 有约 30% 开销，
实际可上传上限约 **4.5MB**。7 份档案超限，直传会被网关直接拒绝（413），
不是函数代码能绕过的。

Blobs 本身单对象能到 5GB，但数据进出都要经过函数那道 6MB 的墙。

## 分片协议（推荐）

沿用现有 `server` 分支的 Netlify 函数和 v2 所有权模型（owner key + 403 越权拒绝），
新增三个端点：

```
POST /api/archive/init      {id, size, parts, sha256}  -> 建立会话，返回 uploadId
PUT  /api/archive/part      {uploadId, index, data}    -> 存一片（每片 ≤ 3MB）
POST /api/archive/finish    {uploadId}                  -> 合并成 Blob，校验 sha256
GET  /api/archive/list      ?since=<ISO>               -> 待同步清单（只含元数据）
GET  /api/archive/pull      ?id=<id>                    -> 分片拉回（响应流式）
DELETE /api/archive/pull    ?id=<id>                    -> 同步完成后删除
```

约束：
- 分片 3MB（base64 后约 4MB，留出余量），一份 7.9MB PDF 约 3 片；
- 会话本身存 Blobs，带 `expiresAt`，避免半截会话堆积；
- `finish` 时校验总 sha256，不一致直接丢弃，不让损坏文件落到电脑；
- 所有权：只有建档的 owner key 能 `finish` / `delete`，他人只能 `pull`。

## 电脑端落盘

收到全部分片并校验后，调用 `app.services.phone_sync.receive_batch()`：

- 落点固定为 `~/同步自手机/YYYY-MM-DD/`；
- 用 SHA-256 判重，同名同内容不落第二份；同名不同内容自动加 `(2)` 后缀，不覆盖；
- 文件名不合规的照常保存，但列入 `rejected` 提示用户改名——不静默丢；
- 落盘后把该目录登记进 `workspaceDirs`，上传页下次扫描自动发现，无需手动加来源目录。

## 同步完成后删云端

电脑端确认落盘 + 结构检查通过后，对每个 id 调 `DELETE /api/archive/pull`。
失败不回滚本地文件（本地才是最终存档），只记日志待重试。

## 为什么不选公网映射

Netlify 只做静态托管 + 无状态函数，没有反向隧道。要让公网请求打进家里电脑，
必须额外跑 `cloudflared tunnel` 或 frp 常驻进程，并配动态域名/鉴权。
这引入一台必须一直开机的电脑、一个第三方隧道账号和一套新的故障点，
与「垃圾 CPU 也能跑、尽量零依赖」的目标相悖。分片中转用现成的 Netlify 站点，
不需要新增任何服务。
