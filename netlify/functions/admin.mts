import type { Config } from "@netlify/functions";
import {
  CODE_RE,
  adminAuthorized,
  json,
  loadState,
  mutateState,
  liveStats,
} from "./_shared.mjs";

/**
 * 后台：提取码、同步设备、中转存储。公开只读接口只给汇总数字。
 * 管理接口用 ADMIN_TOKEN 头鉴权；环境变量没配时管理接口不可用。
 */
export default async (req: Request): Promise<Response> => {
  const url = new URL(req.url);
  const action = url.searchParams.get("action") ?? "";

  if (action === "public" && req.method === "GET") {
    return json(await liveStats(await loadState()));
  }

  if (!adminAuthorized(req)) {
    return json({ ok: false, error: "需要管理口令" }, 401);
  }

  if (action === "session" && req.method === "GET") {
    const state = await loadState();
    return json({
      ok: true,
      settings: state.settings,
      codes: Object.values(state.codes).sort((a, b) => b.lastSeenAt.localeCompare(a.lastSeenAt)),
      devices: Object.values(state.devices).sort((a, b) => b.lastSeenAt.localeCompare(a.lastSeenAt)),
      stats: await liveStats(state),
    });
  }

  if (action === "relay" && req.method === "POST") {
    let body: Record<string, unknown>;
    try {
      body = await req.json();
    } catch {
      return json({ ok: false, error: "请求体不是合法 JSON" }, 400);
    }
    const provider = body.provider;
    if (provider !== "netlify" && provider !== "r2") {
      return json({ ok: false, error: "中转存储只能是 Netlify 或 Cloudflare R2" }, 400);
    }
    const bucket = String(body.r2Bucket ?? "").trim().slice(0, 120);
    if (provider === "r2" && !bucket && !process.env.R2_BUCKET) {
      return json({ ok: false, error: "填写 R2 存储桶名称" }, 400);
    }
    if (provider === "r2"
        && (!process.env.R2_ACCOUNT_ID || !process.env.R2_ACCESS_KEY_ID
            || !process.env.R2_SECRET_ACCESS_KEY)) {
      return json({ ok: false, error: "R2 凭据未配置到站点环境变量" }, 400);
    }
    const settings = await mutateState((state) => {
      state.settings = {
        relay: provider,
        r2Bucket: bucket,
        updatedAt: new Date().toISOString(),
      };
      return state.settings;
    });
    return json({ ok: true, settings });
  }

  if (action === "code" && req.method === "POST") {
    let body: Record<string, unknown>;
    try {
      body = await req.json();
    } catch {
      return json({ ok: false, error: "请求体不是合法 JSON" }, 400);
    }
    const code = String(body.code ?? "").toUpperCase();
    if (!CODE_RE.test(code)) {
      return json({ ok: false, error: "提取码格式不对" }, 400);
    }
    const record = await mutateState((state) => {
      const current = state.codes[code];
      if (!current) return null;
      current.relayAllowed = body.relayAllowed === true;
      return current;
    });
    if (!record) return json({ ok: false, error: "没有这个提取码" }, 404);
    return json({ ok: true, code: record.code, relayAllowed: record.relayAllowed });
  }

  if (action === "code" && req.method === "DELETE") {
    const code = (url.searchParams.get("id") ?? "").toUpperCase();
    if (!CODE_RE.test(code)) return json({ ok: false, error: "提取码格式不对" }, 400);
    const removed = await mutateState((state) => {
      const existed = Boolean(state.codes[code]);
      delete state.codes[code];
      for (const [id, device] of Object.entries(state.devices)) {
        if (device.code === code) delete state.devices[id];
      }
      return existed;
    });
    return json({ ok: true, removed });
  }

  return json({ ok: false, error: "不支持的请求" }, 405);
};

export const config: Config = { path: "/api/admin" };
