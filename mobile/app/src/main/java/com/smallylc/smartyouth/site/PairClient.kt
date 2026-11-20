package com.smallylc.smartyouth.site

import com.smallylc.smartyouth.app
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/**
 * 电脑端「接收手机档案」的配对客户端，对应 docs/app-pair-protocol.md v1。
 *
 * 流程：扫码拿到 base + pin → 配对换 token → 拉已有摘要 → 只传缺的 → 报结束。
 * 所有请求都打同一台电脑的 host:port，token 走 query。
 */
class PairClient(val base: String, val pin: String, val configCode: String) {

    private val http = app.okhttpClient
    private val json = "application/json; charset=utf-8".toMediaType()

    data class Paired(val token: String)

    sealed interface Result<out T> {
        data class Ok<T>(val value: T) : Result<T>
        data class Fail(val message: String, val kind: String = "") : Result<Nothing>
    }

    private fun post(path: String, body: String? = null, share: File? = null): JSONObject? {
        val builder = Request.Builder().url("$base$path").post(
            when {
                share != null -> share.asRequestBody("application/pdf".toMediaType())
                body != null -> body.toRequestBody(json)
                else -> ByteArray(0).toRequestBody(json)
            }
        )
        val response = runCatching { http.newCall(builder.build()).execute() }.getOrNull() ?: return null
        val text = runCatching { response.body.string() }.getOrNull().orEmpty()
        return runCatching { JSONObject(text) }.getOrNull()
    }

    /** 配对：pin 错让用户刷新二维码，config 错让用户核对提取码。 */
    fun pair(device: String): Result<String> {
        val payload = JSONObject()
            .put("pin", pin)
            .put("device", device)
            .put("configCode", configCode)
            .toString()
        val obj = post("/api/pair", payload)
            ?: return Result.Fail("连不上电脑，确认两边在同一 Wi-Fi")
        if (obj.optBoolean("ok")) {
            val token = obj.optString("token")
            return if (token.isBlank()) Result.Fail("电脑没有返回会话凭证") else Result.Ok(token)
        }
        return when (obj.optString("error")) {
            "pin" -> Result.Fail("配对码不对，刷新电脑上的二维码", "pin")
            "config" -> Result.Fail("配置提取码不一致，核对后重填", "config")
            else -> Result.Fail(obj.optString("reason").ifBlank { "配对失败" })
        }
    }

    /** 电脑上已有档案的 SHA-256，用来跳过重复内容。 */
    fun manifest(token: String): Set<String> {
        val obj = post("/api/manifest?token=$token") ?: return emptySet()
        val array = obj.optJSONArray("digests") ?: return emptySet()
        return buildSet {
            for (i in 0 until array.length()) add(array.optString(i))
        }
    }

    fun upload(token: String, file: File): Result<String> {
        val name = java.net.URLEncoder.encode(file.name, "UTF-8")
        val digest = sha256(file)
        val obj = post("/api/upload?token=$token&name=$name&sha256=$digest", share = file)
            ?: return Result.Fail("上传中断")
        if (!obj.optBoolean("ok")) {
            return if (obj.optString("error") == "会话无效，重新扫码配对") Result.Fail("会话已失效，重新配对", "session")
            else Result.Fail(obj.optString("note").ifBlank { obj.optString("error") })
        }
        return when (obj.optString("status")) {
            "ok" -> Result.Ok("已传到电脑")
            "duplicate" -> Result.Ok("电脑已有这份")
            "need_rename" -> Result.Ok("已传，文件名待改")
            else -> Result.Fail(obj.optString("note").ifBlank { "电脑拒收" })
        }
    }

    fun done(token: String) {
        post("/api/done?token=$token")
    }

    companion object {
        /** 二维码载荷形如 http://10.12.16.118:8765/?k=A1B2C3&v=1 */
        fun parseQr(text: String): PairClient? {
            val uri = runCatching { java.net.URI(text.trim()) }.getOrNull() ?: return null
            val host = uri.host ?: return null
            val port = if (uri.port > 0) uri.port else 8765
            val query = uri.rawQuery.orEmpty()
            val k = query.split('&')
                .map { it.split('=', limit = 2) }
                .firstOrNull { it.size == 2 && it[0] == "k" }
                ?.get(1)
                ?: return null
            if (k.isBlank()) return null
            return PairClient("http://$host:$port", k.uppercase(), "")
        }

        fun sha256(file: File): String {
            val digest = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { input ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val read = input.read(buffer)
                    if (read <= 0) break
                    digest.update(buffer, 0, read)
                }
            }
            return digest.digest().joinToString("") { "%02x".format(it) }
        }
    }
}
