import type { Config, Context } from "@netlify/functions";
import { createHash, timingSafeEqual } from "node:crypto";
import { getStore } from "@netlify/blobs";
import { deletePart, getPart, putPart } from "./_relay.mjs";
import { loadState, mutateState, touchCode, touchDevice } from "./_shared.mjs";

/**
 * 中转档案 v1：手机连不上局域网时，把 PDF 分片传到中转站，电脑再分片拉回。
 *
 * 为什么分片：Netlify Functions 的请求与响应都是 buffered 6MB。参考档案单份
 * 1.2~8MB，整份 PUT/GET 会被网关 413；每片 3MB 才能稳定穿过。
 *
 * 鉴权沿用备份 v2：6 位提取码是键，owner key 的 SHA-256 存在索引里。
 * 读（list/pull）只要 code——它本就是配对时双方核对过的分享码；
 * 写（init/part/finish/clear）必须带 owner key，别人无法篡改或删除。
 *
 * 存储布局（同一个 blobs store，strong 一致性）：
 *   archive:<code>                  -> { files: [...], owner, updatedAt }
 *   archive:<code>:<fileId>:<part>  -> 分片原始字节（type: data）
 * finish 只在内存里合并校验摘要，不额外存整份副本——分片即唯一数据源。
 */
const CODE_RE = /^[A-Z2-9]{6}$/;
const KEY_RE = /^[0-9a-f]{64}$/;
const ID_RE = /^[0-9a-zA-Z_-]{1,64}$/;
const PART_BYTES = 3 * 1024 * 1024;
const MAX_INDEX_FILES = 500;

const hits = new Map<string, { count: number; resetAt: number }>();
function throttled(ip: string): boolean {
  const now = Date.now();
  const entry = hits.get(ip);
  if (!entry || entry.resetAt < now) {
    hits.set(ip, { count: 1, resetAt: now + 60_000 });
    return false;
  }
  entry.count += 1;
  return entry.count > 120;          // 分片上传请求多，限流放宽但仍在
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function bytes(data: Uint8Array, status = 200): Response {
  return new Response(data as BodyInit, {
    status,
    headers: { "content-type": "application/octet-stream", "cache-control": "no-store" },
  });
}

function clientIp(req: Request): string {
  return req.headers.get("x-nf-client-connection-ip") ?? req.headers.get("x-forwarded-for") ?? "unknown";
}

function sha256Hex(data: Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}

function ownerMatches(stored: string | undefined, ownerKey: string): boolean {
  if (!stored) return false;
  const a = Buffer.from(stored, "hex");
  const b = Buffer.from(sha256Hex(Buffer.from(ownerKey)), "hex");
  return a.length === b.length && timingSafeEqual(a, b);
}

type ArchiveFile = {
  id: string; name: string; size: number; sha256: string;
  parts: number; uploadedAt: string; ready: boolean;
};
type ArchiveIndex = { files: ArchiveFile[]; owner: string; updatedAt: string };

export default async (req: Request, _context: Context) => {
  const url = new URL(req.url);
  const code = (url.searchParams.get("id") ?? "").toUpperCase();
  const action = url.searchParams.get("action") ?? "";
  const fileId = url.searchParams.get("file") ?? "";

  if (!CODE_RE.test(code)) {
    return json({ ok: false, error: "提取码格式不对" }, 400);
  }
  if (throttled(clientIp(req))) {
    return json({ ok: false, error: "请求过于频繁，请稍后再试" }, 429);
  }

  // consistency: strong——init 写完立刻 part/finish 必须读到同一份索引
  const store = getStore({ name: "smartyouth-archive", consistency: "strong" });
  const adminState = await loadState();
  if (adminState.codes[code]?.relayAllowed === false) {
    return json({ ok: false, error: "提取码未允许中转" }, 403);
  }
  const activity = () => mutateState((state) => {
    touchCode(state, code);
    touchDevice(state, req, code);
  });
  const indexKey = `archive:${code}`;
  const loadIndex = async () =>
    (await store.get(indexKey, { type: "json" })) as ArchiveIndex | null;
  const saveIndex = async (idx: ArchiveIndex) => {
    idx.updatedAt = new Date().toISOString();
    await store.setJSON(indexKey, idx);
  };
  const ownerKey = req.headers.get("x-owner-key") ?? "";
  const ownerOk = (idx: ArchiveIndex | null) =>
    idx !== null && ownerMatches(idx.owner, ownerKey);

  // ---- 读：清单与分片拉取（只需 code）----
  if (req.method === "GET" && action === "list") {
    await activity();
    const idx = await loadIndex();
    const files = (idx?.files ?? []).filter((f) => f.ready).map((f) => ({
      id: f.id, name: f.name, size: f.size, sha256: f.sha256,
      parts: f.parts, uploadedAt: f.uploadedAt,
    }));
    return json({ ok: true, files });
  }

  if (req.method === "GET" && action === "pull") {
    if (!ID_RE.test(fileId)) return json({ ok: false, error: "文件 id 格式不对" }, 400);
    await activity();
    const idx = await loadIndex();
    const file = idx?.files.find((f) => f.id === fileId);
    if (!file || !file.ready) return json({ ok: false, error: "文件不存在或未完成" }, 404);
    const part = Number(url.searchParams.get("part") ?? "0");
    if (!Number.isInteger(part) || part < 0 || part >= file.parts) {
      return json({ ok: false, error: "分片序号超出范围" }, 400);
    }
    const chunk = await getPart(adminState, code, fileId, part);
    if (!chunk) return json({ ok: false, error: "分片丢失，请手机端重传" }, 404);
    return bytes(chunk);
  }

  // ---- 写：分片上传（需 owner key）----
  if ((req.method === "PUT" || req.method === "POST") && action === "init") {
    await activity();
    if (!KEY_RE.test(ownerKey)) {
      return json({ ok: false, error: "缺少所有权凭据" }, 403);
    }
    let body: Record<string, unknown>;
    try { body = await req.json(); } catch {
      return json({ ok: false, error: "请求体不是合法 JSON" }, 400);
    }
    const id = String(body.id ?? "");
    const name = String(body.name ?? "");
    const size = Number(body.size);
    const parts = Number(body.parts);
    const sha = String(body.sha256 ?? "").toLowerCase();
    if (!ID_RE.test(id) || !name.endsWith(".pdf") || !/^[0-9a-f]{64}$/.test(sha)
        || !Number.isInteger(size) || size <= 0 || size > 200 * 1024 * 1024
        || !Number.isInteger(parts) || parts < 1 || parts > 100) {
      return json({ ok: false, error: "init 参数不合法" }, 400);
    }
    let idx = await loadIndex();
    if (idx && !ownerMatches(idx.owner, ownerKey)) {
      return json({ ok: false, error: "这个提取码属于别人" }, 403);
    }
    if (!idx) idx = { files: [], owner: sha256Hex(Buffer.from(ownerKey)), updatedAt: "" };
    // 同名同摘要已存在就复用，避免重复占空间
    const existing = idx.files.find((f) => f.sha256 === sha);
    if (existing) return json({ ok: true, id: existing.id, exists: true });
    if (idx.files.length >= MAX_INDEX_FILES) {
      return json({ ok: false, error: "中转站文件数已达上限，请先让电脑接收清理" }, 507);
    }
    idx.files.push({ id, name: name.slice(0, 200), size, sha256: sha,
                     parts, uploadedAt: new Date().toISOString(), ready: false });
    await saveIndex(idx);
    return json({ ok: true, id });
  }

  if ((req.method === "PUT" || req.method === "POST") && action === "part") {
    if (!KEY_RE.test(ownerKey) || !ID_RE.test(fileId)) {
      return json({ ok: false, error: "缺少凭据或文件 id" }, 403);
    }
    const part = Number(url.searchParams.get("part") ?? "-1");
    const idx = await loadIndex();
    if (!ownerOk(idx)) return json({ ok: false, error: "无权写入这个提取码" }, 403);
    const file = idx!.files.find((f) => f.id === fileId);
    if (!file) return json({ ok: false, error: "文件未 init" }, 404);
    if (!Number.isInteger(part) || part < 0 || part >= file.parts) {
      return json({ ok: false, error: "分片序号超出范围" }, 400);
    }
    const chunk = new Uint8Array(await req.arrayBuffer());
    if (chunk.length > PART_BYTES + 1024) {
      return json({ ok: false, error: "分片超过 3MB" }, 413);
    }
    await putPart(adminState, code, fileId, part, chunk);
    return json({ ok: true });
  }

  if ((req.method === "PUT" || req.method === "POST") && action === "finish") {
    if (!KEY_RE.test(ownerKey) || !ID_RE.test(fileId)) {
      return json({ ok: false, error: "缺少凭据或文件 id" }, 403);
    }
    const idx = await loadIndex();
    if (!ownerOk(idx)) return json({ ok: false, error: "无权写入这个提取码" }, 403);
    const file = idx!.files.find((f) => f.id === fileId);
    if (!file) return json({ ok: false, error: "文件未 init" }, 404);
    // 合并分片：读取每片拼成完整字节，校验 sha256 后才标记 ready
    const pieces: Uint8Array[] = [];
    let total = 0;
    for (let i = 0; i < file.parts; i++) {
      const piece = await getPart(adminState, code, fileId, i);
      if (!piece) return json({ ok: false, error: `缺少分片 ${i}，请重传` }, 409);
      pieces.push(piece);
      total += piece.length;
    }
    if (total !== file.size) {
      return json({ ok: false, error: "合并后大小与 init 声明不符" }, 409);
    }
    const merged = new Uint8Array(total);
    let offset = 0;
    for (const piece of pieces) { merged.set(piece, offset); offset += piece.length; }
    if (sha256Hex(merged) !== file.sha256) {
      return json({ ok: false, error: "内容摘要不符，文件可能传损坏了" }, 409);
    }
    file.ready = true;
    await saveIndex(idx!);
    await mutateState((state) => {
      const record = touchCode(state, code);
      record.relayFiles += 1;
      record.relayBytes += total;
    });
    return json({ ok: true, id: fileId, size: total });
  }

  if (req.method === "DELETE" && action === "clear") {
    if (!KEY_RE.test(ownerKey) || !ID_RE.test(fileId)) {
      return json({ ok: false, error: "缺少凭据或文件 id" }, 403);
    }
    const idx = await loadIndex();
    if (!ownerOk(idx)) return json({ ok: false, error: "无权删除这个提取码的文件" }, 403);
    const pos = idx!.files.findIndex((f) => f.id === fileId);
    if (pos < 0) return json({ ok: true });   // 已删过，幂等
    const file = idx!.files[pos];
    for (let i = 0; i < file.parts; i++) {
      await deletePart(adminState, code, fileId, i);
    }
    idx!.files.splice(pos, 1);
    await saveIndex(idx!);
    await mutateState((state) => {
      const record = touchCode(state, code);
      record.relayFiles = Math.max(0, record.relayFiles - 1);
      record.relayBytes = Math.max(0, record.relayBytes - file.size);
    });
    return json({ ok: true });
  }

  return json({ ok: false, error: "不支持的请求" }, 405);
};

export const config: Config = { path: "/api/archive" };
