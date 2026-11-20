package com.smallylc.smartyouth.data

import android.util.Base64
import com.smallylc.smartyouth.app
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.security.spec.KeySpec
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec

/**
 * 从云端备份拉取支部账号，对应桌面端 app/services/cloud_backup.py 的协议 v2。
 *
 * 客户端解密：密钥 = PBKDF2-SHA256("提取码:密码", salt, 200000)，明文用 AES-256-GCM。
 * 服务端只存密文，提取码错、密码错、内容被改都会在解密这一步失败。
 * v1 老信封只由提取码派生密钥，仍然能解开。
 */
object CloudSync {

    const val DEFAULT_SERVER = "https://smartylc.netlify.app"

    sealed interface Result {
        data class Ok(val names: List<String>, val profileCount: Int) : Result
        data class Fail(val message: String) : Result
    }

    fun sync(server: String, rawCode: String, password: String): Result {
        val code = normalizeCode(rawCode)
        if (code.length != 6) return Result.Fail("提取码是 6 位")
        if (password.isNotEmpty() && !password.matches(Regex("\\d{4}"))) {
            return Result.Fail("密码是 4 位数字")
        }
        val base = normalizeServer(server)
        val request = Request.Builder().url("$base/api/backup?id=$code").get().build()
        val response = runCatching { app.okhttpClient.newCall(request).execute() }.getOrNull()
            ?: return Result.Fail("连不上云端，检查网络")

        response.use { resp ->
            if (resp.code == 404) return Result.Fail("没有这份备份，核对提取码")
            if (!resp.isSuccessful) return Result.Fail("云端返回 ${resp.code}")
            val envelope = runCatching { JSONObject(resp.body.string()) }.getOrNull()
                ?: return Result.Fail("云端返回的内容看不懂")
            val plain = runCatching { decrypt(envelope, code, password) }.getOrElse { error ->
                return Result.Fail(error.message ?: "解不开这份备份")
            }
            val config = runCatching { JSONObject(String(plain, Charsets.UTF_8)) }.getOrNull()
                ?: return Result.Fail("备份内容不是配置文件")
            val array = config.optJSONArray("accounts") ?: JSONArray()
            val names = mutableListOf<String>()
            for (i in 0 until array.length()) {
                val item = array.optJSONObject(i) ?: continue
                val name = item.optString("userName").trim()
                if (name.isBlank()) continue
                Repo.accounts.upsert(
                    Account(
                        userName = name,
                        password = item.optString("password"),
                        remark = item.optString("remark"),
                    )
                )
                names.add(name)
            }
            if (names.isEmpty()) return Result.Fail("这份备份里没有支部账号")
            return Result.Ok(names, config.optJSONArray("cloudProfiles")?.length() ?: 0)
        }
    }

    fun normalizeCode(raw: String): String =
        raw.trim().uppercase().filter { it.isLetterOrDigit() }.take(6)

    private fun normalizeServer(raw: String): String {
        var value = raw.trim().ifBlank { DEFAULT_SERVER }
        if (!value.startsWith("http")) value = "https://$value"
        return value.trimEnd('/')
    }

    private fun decrypt(envelope: JSONObject, code: String, password: String): ByteArray {
        val version = envelope.optInt("v", 1)
        val salt = Base64.decode(envelope.getString("salt"), Base64.NO_WRAP)
        val nonce = Base64.decode(envelope.getString("nonce"), Base64.NO_WRAP)
        val sealed = Base64.decode(envelope.getString("ciphertext"), Base64.NO_WRAP)
        val secret = if (version >= 2) {
            if (password.isBlank()) throw IllegalStateException("这份备份需要 4 位密码")
            "$code:$password"
        } else {
            code
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            SecretKeySpec(derive(secret, salt), "AES"),
            GCMParameterSpec(128, nonce),
        )
        return runCatching { cipher.doFinal(sealed) }.getOrElse {
            throw IllegalStateException("提取码或密码不对")
        }
    }

    private fun derive(secret: String, salt: ByteArray): ByteArray {
        val spec: KeySpec = PBEKeySpec(secret.toCharArray(), salt, 200_000, 256)
        return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
    }
}
