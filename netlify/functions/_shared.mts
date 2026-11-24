import { createHash, timingSafeEqual } from "node:crypto";
import { getStore } from "@netlify/blobs";
import { deletePart } from "./_relay.mjs";

export const CODE_RE = /^[A-Z2-9]{6}$/;
export const KEY_RE = /^[0-9a-f]{64}$/;
export type RelayProvider = "netlify" | "r2";

export type CodeRecord = {
  code: string;
  boundAt: string;
  lastSeenAt: string;
  relayAllowed: boolean;
  backupUpdatedAt: string;
  relayFiles: number;
  relayBytes: number;
};

export type DeviceRecord = {
  id: string;
  name: string;
  platform: string;
  code: string;
  firstSeenAt: string;
  lastSeenAt: string;
};

export type State = {
  codes: Record<string, CodeRecord>;
  devices: Record<string, DeviceRecord>;
  settings: { relay: RelayProvider; r2Bucket: string; updatedAt: string };
};

const empty = (): State => ({
  codes: {},
  devices: {},
  settings: { relay: "netlify", r2Bucket: "", updatedAt: "" },
});

const stateStore = getStore({ name: "smartyouth-admin", consistency: "strong" });
const STATE_KEY = "state";

export async function loadState(): Promise<State> {
  const raw = (await stateStore.get(STATE_KEY, { type: "json" })) as Partial<State> | null;
  if (!raw) return empty();
  const base = empty();
  return {
    codes: raw.codes ?? base.codes,
    devices: raw.devices ?? base.devices,
    settings: { ...base.settings, ...(raw.settings ?? {}) },
  };
}

export async function saveState(state: State): Promise<void> {
  await stateStore.setJSON(STATE_KEY, state);
}

export async function mutateState<T>(change: (state: State) => T): Promise<T> {
  const state = await loadState();
  const result = change(state);
  await saveState(state);
  return result;
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

export function sha256Hex(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

export function adminAuthorized(req: Request): boolean {
  const expected = process.env.ADMIN_TOKEN ?? "";
  if (!expected) return false;
  const got = req.headers.get("x-admin-token") ?? "";
  const a = Buffer.from(got);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

function safeId(value: string, max: number): string {
  return value.replace(/[^\w.-]/g, "").slice(0, max);
}

export function touchCode(
  state: State,
  code: string,
  extra: Partial<CodeRecord> = {},
): CodeRecord {
  const now = new Date().toISOString();
  const current = state.codes[code] ?? {
    code,
    boundAt: now,
    lastSeenAt: now,
    relayAllowed: true,
    backupUpdatedAt: "",
    relayFiles: 0,
    relayBytes: 0,
  };
  const record: CodeRecord = { ...current, ...extra, code, lastSeenAt: now };
  state.codes[code] = record;
  return record;
}

/**
 * 登记设备。`knownId` 用于没有设备头的旧客户端——备份接口只有所有权凭据，
 * 同一个凭据就是同一台机器，用它区分设备比按 UA 合并更准。
 */
export function touchDevice(state: State, req: Request, code: string, knownId = ""): void {
  const id = safeId(req.headers.get("x-device-id") ?? "", 64) || safeId(knownId, 64);
  const ua = (req.headers.get("user-agent") ?? "").slice(0, 120);
  if (!id && !ua) return;
  const key = id || sha256Hex(`${ua}|${code}`).slice(0, 16);
  const now = new Date().toISOString();
  const platform = safeId(req.headers.get("x-device-platform") ?? "client", 24) || "client";
  const name = (req.headers.get("x-device-name") ?? "").slice(0, 40) || platform;
  const current = state.devices[key];
  state.devices[key] = {
    id: key,
    name: current?.name ?? name,
    platform: current?.platform ?? platform,
    code: current?.code ?? code,
    firstSeenAt: current?.firstSeenAt ?? now,
    lastSeenAt: now,
  };
}

const backupStore = getStore({ name: "smartyouth-backup", consistency: "strong" });
const archiveStore = getStore({ name: "smartyouth-archive", consistency: "strong" });

/**
 * 删掉一个提取码的全部云端数据：备份信封、中转索引与分片。
 * 管理页的「移除」必须落到这里，否则列表按存储生成，刷新后那一行会回来。
 */
export async function deleteCodeData(
  state: State,
  code: string,
): Promise<{ backup: boolean; files: number; parts: number }> {
  const backup = await backupStore.get(code);
  if (backup) await backupStore.delete(code);

  const indexKey = `archive:${code}`;
  const index = (await archiveStore.get(indexKey, { type: "json" })) as
    | { files?: { id: string; parts?: number }[] }
    | null;
  let files = 0;
  let parts = 0;
  for (const file of index?.files ?? []) {
    for (let i = 0; i < (file.parts ?? 0); i += 1) {
      try {
        await deletePart(state, code, file.id, i);
        parts += 1;
      } catch {
        // 分片可能已在上次清理中删掉，继续删剩下的
      }
    }
    files += 1;
  }
  if (index) await archiveStore.delete(indexKey);
  return { backup: Boolean(backup), files, parts };
}

/**
 * 存储里真实存在的提取码。管理页照着它列，老备份不必等设备再连一次才会出现。
 */
export async function listStoredCodes(): Promise<{ code: string; updatedAt: string }[]> {
  const out: { code: string; updatedAt: string }[] = [];
  for await (const page of backupStore.list({ paginate: true })) {
    for (const blob of page.blobs) {
      const code = blob.key.toUpperCase();
      if (!CODE_RE.test(code)) continue;
      const raw = (blob as unknown as { lastModified?: Date | string }).lastModified;
      let updatedAt =
        raw instanceof Date
          ? raw.toISOString()
          : typeof raw === "string" && raw
            ? new Date(raw).toISOString()
            : "";
      if (!updatedAt) {
        const record = (await backupStore.get(code, { type: "json" })) as
          | { updatedAt?: string }
          | null;
        updatedAt = record?.updatedAt ?? "";
      }
      out.push({ code, updatedAt });
    }
  }
  return out;
}

type IndexFile = { ready: boolean; size: number };

/** 公开统计直接数存储，历史备份和升级前上传的中转文件都算进来。 */
export async function liveStats(state: State) {
  let backups = 0;
  for await (const page of backupStore.list({ paginate: true })) {
    backups += page.blobs.filter((blob) => CODE_RE.test(blob.key)).length;
  }

  let relayFiles = 0;
  let relayBytes = 0;
  for await (const page of archiveStore.list({ paginate: true, prefix: "archive:" })) {
    for (const blob of page.blobs) {
      const parts = blob.key.split(":");
      if (parts.length !== 2 || !CODE_RE.test(parts[1])) continue;
      const index = (await archiveStore.get(blob.key, { type: "json" })) as
        | { files?: IndexFile[] }
        | null;
      for (const file of index?.files ?? []) {
        if (!file.ready) continue;
        relayFiles += 1;
        relayBytes += file.size;
      }
    }
  }

  return {
    backups,
    relayFiles,
    relayBytes,
    devices: Object.keys(state.devices).length,
    updatedAt: new Date().toISOString(),
  };
}
