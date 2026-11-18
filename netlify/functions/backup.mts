import type { Config, Context } from "@netlify/functions";
import { getStore } from "@netlify/blobs";

/**
 * 云端备份：以 6 位提取码为键存取一份**已加密**的信封。
 * 服务端不掌握密钥，也看不懂内容；它只是把密文放在键上。
 */
const CODE_RE = /^[A-Z2-9]{6}$/;
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

export default async (req: Request, _context: Context) => {
  const url = new URL(req.url);
  const code = (url.searchParams.get("id") ?? "").toUpperCase();

  if (!CODE_RE.test(code)) {
    return json({ ok: false, error: "提取码必须是 6 位大写字母或数字（不含 0/1/I/O）" }, 400);
  }
  if (throttled(clientIp(req))) {
    return json({ ok: false, error: "请求过于频繁，请稍后再试" }, 429);
  }

  const store = getStore("smartyouth-backup");

  if (req.method === "GET") {
    const stored = await store.get(code, { type: "json" });
    if (!stored) return json({ ok: false, error: "没有找到这份备份" }, 404);
    return json(stored);
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
    await store.setJSON(code, { ...envelope, updatedAt: new Date().toISOString() });
    return json({ ok: true, code });
  }

  return json({ ok: false, error: "只支持 GET / PUT" }, 405);
};

export const config: Config = { path: "/api/backup" };
