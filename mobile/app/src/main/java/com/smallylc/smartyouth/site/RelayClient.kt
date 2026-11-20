package com.smallylc.smartyouth.site

import android.content.Context
import com.smallylc.smartyouth.app
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.security.SecureRandom
import java.util.UUID

/**
 * 中转站上传（协议第 9 节）：局域网连不上时，用同一个配置提取码把 PDF 分片传上去，
 * 电脑端轮询取回。每片 3MB，最后一片按实际大小；finish 时服务端校验摘要。
 *
 * 写操作要带 owner key：它是本机第一次走中转时生成的 64 位随机值，服务端只存哈希。
 */
class RelayClient(
    private val server: String,
    private val code: String,
) {

    private val http = app.okhttpClient
    private val jsonType = "application/json; charset=utf-8".toMediaType()
    private val octet = "application/octet-stream".toMediaType()

    sealed interface Result {
        data class Ok(val uploaded: Int, val skipped: Int) : Result
        data class Fail(val message: String) : Result
    }

    private data class Resp(val code: Int, val body: JSONObject?, val bytes: ByteArray?)

    private fun call(
        method: String,
        query: String,
        body: ByteArray? = null,
        type: okhttp3.MediaType = jsonType,
    ): Resp {
        val builder = Request.Builder()
            .url("$server/api/archive?id=$code&$query")
            .header("X-Owner-Key", ownerKey())
        when (method) {
            "GET" -> builder.get()
            "DELETE" -> builder.delete()
            else -> builder.put((body ?: ByteArray(0)).toRequestBody(type))
        }
        val response = runCatching { http.newCall(builder.build()).execute() }.getOrNull()
            ?: return Resp(0, null, null)
        response.use { resp ->
            val raw = runCatching { resp.body.bytes() }.getOrNull() ?: ByteArray(0)
            val obj = runCatching { JSONObject(String(raw, Charsets.UTF_8)) }.getOrNull()
            return Resp(resp.code, obj, raw)
        }
    }

    fun upload(files: List<File>, onStep: (Int, Int, String) -> Unit): Result {
        var uploaded = 0
        var skipped = 0
        files.forEachIndexed { index, file ->
            onStep(index, files.size, file.name)
            val sha = PairClient.sha256(file)
            val size = file.length()
            val parts = if (size == 0L) 1 else ((size + CHUNK - 1) / CHUNK).toInt()
            val id = UUID.randomUUID().toString().replace("-", "")

            val declared = JSONObject()
                .put("id", id)
                .put("name", file.name)
                .put("size", size)
                .put("sha256", sha)
                .put("parts", parts)
            val init = call("PUT", "action=init", declared.toString().toByteArray(Charsets.UTF_8))
            if (init.code == 0) return Result.Fail("连不上中转站")
            if (init.body?.optBoolean("exists") == true) {
                skipped += 1
                return@forEachIndexed
            }
            if (init.code !in 200..299) {
                return Result.Fail(init.body?.optString("error").orEmpty().ifBlank { "中转站拒绝了这份档案" })
            }

            file.inputStream().use { input ->
                val buffer = ByteArray(CHUNK)
                var partIndex = 0
                while (true) {
                    var filled = 0
                    while (filled < CHUNK) {
                        val read = input.read(buffer, filled, CHUNK - filled)
                        if (read <= 0) break
                        filled += read
                    }
                    if (filled == 0) break
                    val chunk = buffer.copyOf(filled)
                    val part = call(
                        "PUT",
                        "action=part&file=$id&part=$partIndex",
                        chunk,
                        octet,
                    )
                    if (part.code !in 200..299) {
                        return Result.Fail("第 ${partIndex + 1} 片没传上去")
                    }
                    partIndex += 1
                    if (filled < CHUNK) break
                }
            }

            val finish = call("PUT", "action=finish&file=$id", "{}".toByteArray(Charsets.UTF_8))
            if (finish.code !in 200..299) {
                return Result.Fail(finish.body?.optString("error").orEmpty().ifBlank { "中转站合并失败" })
            }
            uploaded += 1
        }
        return Result.Ok(uploaded, skipped)
    }

    private fun ownerKey(): String {
        val prefs = app.getSharedPreferences("relay", Context.MODE_PRIVATE)
        prefs.getString("owner_key", null)?.let { if (it.length == 64) return it }
        val bytes = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val key = bytes.joinToString("") { "%02x".format(it) }
        prefs.edit().putString("owner_key", key).apply()
        return key
    }

    companion object {
        private const val CHUNK = 3 * 1024 * 1024
    }
}
