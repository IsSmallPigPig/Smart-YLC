import type { Config } from "@netlify/functions";
import {
  CODE_RE,
  adminAuthorized,
  json,
  loadState,
  mutateState,
  deleteCodeData,
  listStoredCodes,
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
    const codes = Object.values(state.codes);
    const known = new Set(codes.map((record) => record.code));
    for (const stored of await listStoredCodes()) {
      if (known.has(stored.code)) continue;
      // 早于登记逻辑上传的备份只有密文，没有活动记录，按备份时间展示
      codes.push({
        code: stored.code,
        boundAt: stored.updatedAt,
        lastSeenAt: stored.updatedAt,
        relayAllowed: false,
        backupUpdatedAt: stored.updatedAt,
        relayFiles: 0,
        relayBytes: 0,
      });
    }
    codes.sort((a, b) => b.lastSeenAt.localeCompare(a.lastSeenAt));
    return json({
      ok: true,
      settings: state.settings,
      codes,
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
    const relayAllowed = body.relayAllowed === true;
    const stored = await listStoredCodes();
    if (!stored.some((item) => item.code === code)) {
      return json({ ok: false, error: "没有这个提取码" }, 404);
    }
    const record = await mutateState((state) => {
      const now = new Date().toISOString();
      const current = state.codes[code] ?? {
        code,
        boundAt: now,
        lastSeenAt: now,
        relayAllowed: false,
        backupUpdatedAt: now,
        relayFiles: 0,
        relayBytes: 0,
      };
      current.relayAllowed = relayAllowed;
      state.codes[code] = current;
      return current;
    });
    return json({ ok: true, code: record.code, relayAllowed: record.relayAllowed });
  }

  if (action === "code" && req.method === "DELETE") {
    const code = (url.searchParams.get("id") ?? "").toUpperCase();
    if (!CODE_RE.test(code)) return json({ ok: false, error: "提取码格式不对" }, 400);
    const state = await loadState();
    const deleted = await deleteCodeData(state, code);
    await mutateState((next) => {
      delete next.codes[code];
      for (const [id, device] of Object.entries(next.devices)) {
        if (device.code === code) delete next.devices[id];
      }
    });
    return json({ ok: true, removed: true, ...deleted });
  }

  return json({ ok: false, error: "不支持的请求" }, 405);
};

export const config: Config = { path: "/api/admin" };
