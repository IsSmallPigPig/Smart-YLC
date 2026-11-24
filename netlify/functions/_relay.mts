import { DeleteObjectCommand, GetObjectCommand, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { getStore } from "@netlify/blobs";
import type { State } from "./_shared.mjs";

/**
 * 中转分片存哪：Netlify Blobs 或 Cloudflare R2。索引始终留在 Netlify，
 * 这里只管分片字节——切换存储不会改变提取码、摘要校验和接口形状。
 */
const blobs = getStore({ name: "smartyouth-archive", consistency: "strong" });

let cached: { account: string; key: string; secret: string; client: S3Client } | null = null;

function r2Client() {
  const account = process.env.R2_ACCOUNT_ID ?? "";
  const key = process.env.R2_ACCESS_KEY_ID ?? "";
  const secret = process.env.R2_SECRET_ACCESS_KEY ?? "";
  if (!account || !key || !secret) {
    throw new Error("R2 凭据未配置");
  }
  if (cached && cached.account === account && cached.key === key && cached.secret === secret) {
    return cached.client;
  }
  const client = new S3Client({
    region: "auto",
    endpoint: `https://${account}.r2.cloudflarestorage.com`,
    credentials: { accessKeyId: key, secretAccessKey: secret },
  });
  cached = { account, key, secret, client };
  return client;
}

function netlifyKey(code: string, fileId: string, part: number): string {
  return `archive:${code}:${fileId}:${part}`;
}

function r2Key(code: string, fileId: string, part: number): string {
  return `parts/${code}/${fileId}/${part}`;
}

function bucketName(state: State): string {
  const bucket = state.settings.r2Bucket || process.env.R2_BUCKET || "";
  if (!bucket) throw new Error("R2 存储桶未配置");
  return bucket;
}

export async function putPart(
  state: State,
  code: string,
  fileId: string,
  part: number,
  bytes: Uint8Array,
): Promise<void> {
  if (state.settings.relay === "r2") {
    await r2Client().send(new PutObjectCommand({
      Bucket: bucketName(state),
      Key: r2Key(code, fileId, part),
      Body: Buffer.from(bytes),
    }));
    return;
  }
  await blobs.set(netlifyKey(code, fileId, part), bytes, { type: "data" });
}

export async function getPart(
  state: State,
  code: string,
  fileId: string,
  part: number,
): Promise<Uint8Array | null> {
  if (state.settings.relay === "r2") {
    const result = await r2Client().send(new GetObjectCommand({
      Bucket: bucketName(state),
      Key: r2Key(code, fileId, part),
    }));
    if (!result.Body) return null;
    return new Uint8Array(await result.Body.transformToByteArray());
  }
  const blob = await blobs.get(netlifyKey(code, fileId, part), { type: "data" });
  return blob ? new Uint8Array(await blob.arrayBuffer()) : null;
}

export async function deletePart(
  state: State,
  code: string,
  fileId: string,
  part: number,
): Promise<void> {
  if (state.settings.relay === "r2") {
    await r2Client().send(new DeleteObjectCommand({
      Bucket: bucketName(state),
      Key: r2Key(code, fileId, part),
    }));
    return;
  }
  await blobs.delete(netlifyKey(code, fileId, part));
}
