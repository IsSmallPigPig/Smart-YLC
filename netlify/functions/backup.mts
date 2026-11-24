import type { Config, Context } from "@netlify/functions";
import { createHash, timingSafeEqual } from "node:crypto";
import { getStore } from "@netlify/blobs";
import { mutateState, touchCode, touchDevice } from "./_shared.mjs";

/**
 * 云端备份 v2：以 6 位提取码为键存取一份**已加密**的信封。
 *
 * v2 在服务端加了两道门：
 * 1. 所有权——建档时客户端生成 owner key，服务端只存它的 SHA-256。
 *    后续 PUT 必须带同样的 key（403 拒绝陌生人覆盖），GET/probe 不泄露 key。
 * 2. 查重——`?probe=1` 只回答「这个码存在吗」，供建档前检测撞车。
 *
 * 记录格式：{ v, salt, nonce, ciphertext, owner, updatedAt }
 * 旧记录（v1，无 owner 字段）任何人都不再能覆盖，只能维持原样；
 * 客户端仍可解密 v1 信封，老备份不作废。
 */
const CODE_RE = /^[A-Z2-9]{6}$/;
const KEY_RE = /^[0-9a-f]{64}$/;
const MAX_BYTES = 5 * 1024 * 1024;

// 单实例内的简易限流：挡暴力枚举提取码的脚本。多实例部署时这只是第一道。
const hits = new Map<string, { count: number; resetAt: number }>();
function throttled(ip: string): boolean {
  const now = Date.now();
  const entry = hits.get(ip);
  if (!entry || entry.resetAt < now) {
    hits.set(ip, { count: 1, resetAt: now + 60_000 });
    return false;
  }
  entry.count += 1;
  return entry.count > 30;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function clientIp(req: Request): string {
  return req.headers.get("x-nf-client-connection-ip") ?? req.headers.get("x-forwarded-for") ?? "unknown";
}

function sha256Hex(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

function ownerMatches(stored: string | undefined, ownerKey: string): boolean {
  if (!stored) return false;
  const a = Buffer.from(stored, "hex");
  const b = Buffer.from(sha256Hex(ownerKey), "hex");
  return a.length === b.length && timingSafeEqual(a, b);
}

export default async (req: Request, _context: Context) => {
  const url = new URL(req.url);
  const code = (url.searchParams.get("id") ?? "").toUpperCase();

  if (!CODE_RE.test(code)) {
    return json({ ok: false, error: "提取码必须是 6 位大写字母或数字（不含 0/1/I/O）" }, 400);
  }
  if (throttled(clientIp(req))) {
    return json({ ok: false, error: "请求过于频繁，请稍后再试" }, 429);
  }

  // strong：跨区复制默认最终一致——建档刚 PUT 完就 probe/越权校验，读到旧值
  // 会把「别人的码」看成空闲或放行覆盖。备份场景读写必须看到彼此。
  const store = getStore({ name: "smartyouth-backup", consistency: "strong" });

  if (req.method === "GET") {
    const stored = (await store.get(code, { type: "json" })) as
      | (Record<string, unknown> & { owner?: string })
      | null;
    if (url.searchParams.get("probe") === "1") {
      // 建档查重：只回答存在与否，不返回任何内容
      return json({ ok: true, exists: stored !== null });
    }
    if (!stored) return json({ ok: false, error: "没有找到这份备份" }, 404);
    const { owner: _owner, ...envelope } = stored;
    return json(envelope);
  }

  if (req.method === "PUT" || req.method === "POST") {
    const text = await req.text();
    if (text.length > MAX_BYTES) {
      return json({ ok: false, error: "备份超过 5MB 上限" }, 413);
    }
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      return json({ ok: false, error: "请求体不是合法 JSON" }, 400);
    }
    const envelope = body as Record<string, unknown>;
    for (const key of ["salt", "nonce", "ciphertext"]) {
      if (typeof envelope?.[key] !== "string") {
        return json({ ok: false, error: `缺少字段 ${key}` }, 400);
      }
    }
    const ownerKey = req.headers.get("x-owner-key") ?? "";
    if (!KEY_RE.test(ownerKey)) {
      return json({ ok: false, error: "缺少所有权凭据，无法上传" }, 403);
    }
    const prior = (await store.get(code, { type: "json" })) as
      | (Record<string, unknown> & { owner?: string })
      | null;
    if (prior && !ownerMatches(prior.owner, ownerKey)) {
      // 已有档案且 key 对不上：别人的提取码，拒绝覆盖。
      // v1 老记录没有 owner，等价于任何人都不匹配——只能被读取，不能被改写。
      return json({ ok: false, error: "这个提取码的配置属于别人" }, 403);
    }
    const updatedAt = new Date().toISOString();
    await store.setJSON(code, {
      ...envelope,
      owner: sha256Hex(ownerKey),
      updatedAt,
    });
    await mutateState((state) => {
      touchCode(state, code, { backupUpdatedAt: updatedAt });
      // 备份接口没有设备头，用所有权凭据的散列当设备标识：一机一凭据，稳定且不泄露原文。
      touchDevice(state, req, code, `owner-${sha256Hex(ownerKey).slice(0, 12)}`);
    });
    return json({ ok: true, code });
  }

  return json({ ok: false, error: "只支持 GET / PUT" }, 405);
};

export const config: Config = { path: "/api/backup" };
