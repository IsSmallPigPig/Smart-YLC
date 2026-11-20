package com.smallylc.smartyouth.site

import com.smallylc.smartyouth.app
import com.smallylc.smartyouth.data.AccountStore
import okhttp3.FormBody
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/** 一个客户端 = 一个账号的站点会话。Cookie 按账号持久化，切换账号互不干扰。 */
class SiteClient(private val accountStore: AccountStore, val userName: String) {

    private val http: OkHttpClient = app.okhttpClient
    private val password: String
        get() = accountStore.accounts.firstOrNull { it.userName == userName }?.password.orEmpty()

    /** 会话 Cookie，登录后由响应头更新。 */
    private val cookies = LinkedHashMap<String, String>()

    init {
        accountStore.cookieHeader(userName)
            .split("; ")
            .filter { it.contains("=") }
            .forEach {
                val i = it.indexOf('=')
                cookies[it.substring(0, i)] = it.substring(i + 1)
            }
    }

    var loggedIn: Boolean = false
        private set

    // ---------- 基础请求 ----------

    private fun baseRequest(url: String, authorize: Boolean = false): Request.Builder =
        Request.Builder()
            .url(url)
            .header("User-Agent", SiteConstants.USER_AGENT)
            .header("Referer", "${SiteConstants.SITE_BASE_URL}/bg/index_main.html")
            .header("Origin", SiteConstants.SITE_BASE_URL)
            .header("X-Requested-With", "XMLHttpRequest")
            .apply {
                if (cookies.isNotEmpty()) header("Cookie", cookies.entries.joinToString("; ") { "${it.key}=${it.value}" })
                if (authorize) header(SiteConstants.AUTHORIZE_HEADER, userName)
            }

    private fun storeCookies(response: okhttp3.Response) {
        response.headers("set-cookie").forEach { raw ->
            val first = raw.substringBefore(';')
            val i = first.indexOf('=')
            if (i > 0) cookies[first.substring(0, i).trim()] = first.substring(i + 1).trim()
        }
        if (cookies.isNotEmpty()) {
            accountStore.saveCookies(userName, cookies.entries.joinToString("; ") { "${it.key}=${it.value}" })
        }
    }

    private fun call(request: Request, timeoutSec: Int = 20): okhttp3.Response =
        http.newBuilder()
            .callTimeout(timeoutSec.toLong(), TimeUnit.SECONDS)
            .build()
            .newCall(request)
            .execute()
            .also { storeCookies(it) }

    private class SiteException(message: String) : RuntimeException(message)

    /** 站点在维护或网关异常：不是用户的问题，界面要单独说明。 */
    class SiteUnavailableException(message: String) : RuntimeException(message)

    private fun jsonOf(response: okhttp3.Response): JSONObject {
        val body = response.body?.string().orEmpty()
        if (response.code >= 500) throw SiteUnavailableException("站点返回 ${response.code}")
        if (!response.isSuccessful) throw SiteException("HTTP ${response.code}")
        if (body.isBlank()) throw SiteException("站点返回空响应")
        return runCatching { JSONObject(body) }.getOrElse {
            // 被重定向到登录页或防护页时返回 HTML，原样抛出会把界面撑爆
            if (body.trimStart().startsWith("<")) {
                throw SiteUnavailableException("站点正在维护")
            }
            throw SiteException("站点返回的内容无法识别")
        }
    }

    // ---------- 登录 ----------

    /** GET /login/adminLogin；密码错一次就会触发验证码，之后带 loginValidCode 重试。 */
    fun login(loginValidCode: String = ""): LoginVerdict {
        if (password.isBlank()) return LoginVerdict("failed", "未填写账号密码")
        val url = buildString {
            append(SiteConstants.API_BASE_URL).append(SiteConstants.LOGIN_PATH)
            append("?weakPass=").append(if (SiteConstants.isStrongPassword(password)) 0 else 1)
            append("&userName=").append(java.net.URLEncoder.encode(userName, "UTF-8"))
            append("&password=").append(SiteConstants.doubleMd5(password))
            if (loginValidCode.isNotBlank()) {
                append("&loginValidCode=").append(java.net.URLEncoder.encode(loginValidCode, "UTF-8"))
            }
        }
        val raw = runCatching { jsonOf(call(baseRequest(url).get().build())) }
            .getOrElse { error ->
                if (error is SiteUnavailableException) {
                    return LoginVerdict("unavailable", error.message ?: "站点正在维护")
                }
                return LoginVerdict("failed", "网络错误：${error.message}")
            }
        val verdict = parseLogin(raw)
        if (verdict.kind == "ok") {
            loggedIn = true
            accountStore.saveCookies(userName, cookies.entries.joinToString("; ") { "${it.key}=${it.value}" })
        }
        return verdict
    }

    private fun parseLogin(raw: JSONObject): LoginVerdict {
        val status = raw.optString("status")
        val msg = raw.optString("msg", raw.optString("message"))
        if (status == "EXPIREDPASS" || status == "WEAKPASS") {
            return LoginVerdict("passwordChange", msg.ifBlank { "需修改密码后才能登录" })
        }
        if (status == "OK" || status == "200") {
            if (raw.optString("data") == "1") return LoginVerdict("needMemberName", msg.ifBlank { "需要校验成员姓名" })
            return LoginVerdict("ok", msg.ifBlank { "登录成功" })
        }
        if (Regex("验证码|validCode|captcha|错误次数|失败", RegexOption.IGNORE_CASE).containsMatchIn(msg)) {
            return LoginVerdict("captcha", msg)
        }
        return LoginVerdict("failed", msg.ifBlank { "登录失败（status=${status.ifBlank { "空" }}）" })
    }

    /** 图形验证码 PNG，需要站点规定的特殊头。 */
    fun fetchCaptcha(): ByteArray? = runCatching {
        val response = call(
            baseRequest(
                "${SiteConstants.API_BASE_URL}${SiteConstants.CAPTCHA_PATH}?t=${System.currentTimeMillis()}"
            )
                .get()
                .header(SiteConstants.CAPTCHA_HEADER, "Approve")
                .build()
        )
        if (response.isSuccessful && response.header("content-type")?.startsWith("image") == true) {
            response.body?.bytes()
        } else null
    }.getOrNull()

    /** 只读探测会话是否还有效。 */
    fun sessionAccount(): Pair<Boolean, String> = runCatching {
        val raw = jsonOf(call(baseRequest("${SiteConstants.API_BASE_URL}/login/getSessionAccount").get().build()))
        val status = raw.optString("status", raw.optString("code"))
        val msg = raw.optString("msg", raw.optString("message"))
        val expired = msg.contains("失效") || msg.contains("超时") || msg.contains("重新登录") ||
            msg.contains("未登录") || status == "EXPIRE"
        if ((status == "OK" || status == "200") && !expired) {
            loggedIn = true
            val node = raw.optJSONObject("msg") ?: raw
            val user = node.optString("username", node.optString("account"))
            true to user.ifBlank { "会话有效" }
        } else {
            loggedIn = false
            false to msg.ifBlank { "会话已失效" }
        }
    }.getOrElse {
        if (it is SiteUnavailableException) false to "unavailable:${it.message}"
        else false to "会话探测失败：${it.message}"
    }

    // ---------- 组织 / 团员 / 站内信 ----------

    private fun postJson(path: String, payload: JSONObject, authorize: Boolean = true): JSONObject {
        val request = baseRequest(SiteConstants.API_BASE_URL + path, authorize)
            .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()
        return jsonOf(call(request))
    }

    fun orgTree(): List<OrgNode> {
        val raw = postJson("/api/v1/admin/org/getOrgTree", JSONObject())
        val found = LinkedHashMap<String, OrgNode>()

        fun visit(node: Any?, depth: Int) {
            when (node) {
                is JSONArray -> for (i in 0 until node.length()) visit(node.opt(i), depth)
                is JSONObject -> {
                    val oid = node.opt("leagueId") ?: node.opt("oid") ?: node.opt("orgId") ?: node.opt("id")
                    val name = node.opt("leagueName") ?: node.opt("orgName") ?: node.opt("name") ?: node.opt("text")
                    if (oid != null && name != null) {
                        found[oid.toString()] = OrgNode(
                            oid = oid.toString(),
                            name = name.toString(),
                            parentOid = node.optString("parentId"),
                        )
                    }
                    if (depth < 8) {
                        for (key in listOf("leagueList", "list", "rows", "records", "children", "data")) {
                            node.opt(key)?.let { visit(it, depth + 1) }
                        }
                    }
                }
            }
        }

        visit(raw.opt("data"), 0)
        visit(raw.opt("msg"), 0)
        if (found.isEmpty()) {
            for (key in raw.keys()) {
                if (key != "data" && key != "msg") visit(raw.opt(key), 0)
            }
        }
        return found.values.toList()
    }

    fun membersPage(pageNo: Int, pageSize: Int = 100, oid: String = ""): List<Member> {
        val payload = JSONObject().put("page", pageNo).put("pageSize", pageSize)
        if (oid.isNotBlank()) payload.put("oid", oid)
        val raw = postJson("/api/v1/admin/members/page", payload)
        val code = raw.optString("code")
        if (code.isNotEmpty() && code != "200") {
            throw SiteException("团员列表接口返回业务失败：code=$code ${raw.optString("message")}")
        }
        val (rows, _) = MemberParser.pickRows(raw)
        return MemberParser.parseRows(rows, oid)
    }

    fun allMembers(oid: String = "", pageSize: Int = 100): List<Member> {
        val members = ArrayList<Member>()
        var page = 1
        while (true) {
            val batch = membersPage(page, pageSize, oid)
            if (batch.isEmpty()) break
            members += batch
            if (batch.size < pageSize) break
            page += 1
            if (page > 50) break // 防御站点翻页异常
        }
        return members
    }

    fun messagesPage(pageNo: Int, pageSize: Int = 100): List<SiteMessage> {
        val url = "${SiteConstants.API_BASE_URL}/api/v1/admin/message/findAllMessage" +
            "?type=0&pageNo=$pageNo&pageSize=$pageSize"
        val raw = runCatching { jsonOf(call(baseRequest(url).get().build())) }
            .getOrElse { throw SiteException("站内信请求失败：${it.message}") }
        val code = raw.optString("code")
        if (code.isNotEmpty() && code != "200") {
            throw SiteException("站内信接口返回异常：${raw.optString("message", raw.optString("msg", code))}")
        }
        val node = raw.optJSONObject("data") ?: raw.optJSONObject("msg") ?: raw
        val rows = node?.optJSONArray("listData") ?: node?.optJSONArray("list") ?: JSONArray()
        return (0 until rows.length()).mapNotNull { i ->
            val row = rows.optJSONObject(i) ?: return@mapNotNull null
            val title = row.optString("title")
            val content = row.optString("content")
            SiteMessage(
                id = row.optString("id", row.optString("receiveId", "$i")),
                title = MessageParse.cleanTitle(title),
                content = MessageParse.stripHtml(content),
                mobile = MessageParse.extractMobile(content),
                kind = MessageParse.classify(title, content),
                status = row.optInt("status", 1),
                timeStr = row.optString("createTime", row.optString("timeStr")),
            )
        }
    }

    // ---------- 上传（业务必需的写操作） ----------

    data class ValidationItem(val name: String, val ok: Boolean, val msg: String)

    fun validateFileNames(names: List<String>): List<ValidationItem> {
        val payload = JSONObject().put("fileNames", JSONArray(names))
        val raw = postJson("/members/bg/getMemberVarNames", payload)
        val data = raw.optJSONObject("msg")?.optJSONArray("data") ?: return emptyList()
        return (0 until data.length()).mapNotNull { i ->
            val item = data.optJSONObject(i) ?: return@mapNotNull null
            val status = item.optString("status")
            ValidationItem(
                name = item.optString("name"),
                ok = status == "0" || status.uppercase() in setOf("OK", "SUCCESS"),
                msg = item.optString("msg"),
            )
        }
    }

    /** 校验 → 10MB 分片顺序上传 → /file/add 入库。返回 (mid, retpath)。 */
    fun uploadPdf(file: File, displayName: String, onProgress: (Int, Int) -> Unit = { _, _ -> }): Pair<String, String> {
        if (!displayName.lowercase().endsWith(".pdf")) {
            throw SiteException("站点仅支持 .pdf 文件")
        }
        if (file.length() > SiteConstants.SITE_SINGLE_FILE_BYTES) {
            throw SiteException("文件超过站点 10MB 上限，请降低扫描画质后重试")
        }
        val items = validateFileNames(listOf(displayName))
        val bad = items.firstOrNull { !it.ok }
        if (bad != null) throw SiteException("站点校验拒绝：${bad.name} ${bad.msg}")

        val size = file.length()
        val totalChunks = if (size == 0L) 1 else ((size + SiteConstants.CHUNK_BYTES - 1) / SiteConstants.CHUNK_BYTES).toInt()
        val fileId = "file_${System.currentTimeMillis()}_0"

        file.inputStream().use { input ->
            for (index in 0 until totalChunks) {
                val chunkSize = minOf(SiteConstants.CHUNK_BYTES.toLong(), size - index * SiteConstants.CHUNK_BYTES.toLong()).toInt()
                val buffer = ByteArray(chunkSize)
                var read = 0
                while (read < chunkSize) {
                    val n = input.read(buffer, read, chunkSize - read)
                    if (n < 0) throw SiteException("文件读取中断")
                    read += n
                }
                val body = MultipartBody.Builder()
                    .addFormDataPart("file", displayName, buffer)
                    .addFormDataPart("id", value = fileId)
                    .addFormDataPart("chunks", value = totalChunks.toString())
                    .addFormDataPart("chunk", value = index.toString())
                    .addFormDataPart("size", value = chunkSize.toString())
                    .addFormDataPart("name", value = displayName)
                    .addFormDataPart("md5", value = "")
                    .build()
                val response = call(
                    baseRequest("${SiteConstants.API_BASE_URL}/file/breakpoint-upload").post(body.toRequestBody()).build(),
                    timeoutSec = 120,
                )
                val raw = runCatching { jsonOf(response) }.getOrNull()
                if (raw != null) {
                    val status = raw.optString("status", raw.optString("code"))
                    if (status == "ERROR" || status == "1") {
                        throw SiteException("分片 ${index + 1}/$totalChunks 被拒绝：${raw.optString("msg", raw.optString("message"))}")
                    }
                }
                onProgress(index + 1, totalChunks)
            }
        }

        val suffix = displayName.substringAfterLast('.', "").lowercase()
        val raw = postJson("/file/add", JSONObject().put("fileName", displayName).put("suffix", suffix))
        val status = raw.optString("status", raw.optString("code"))
        if (status !in setOf("OK", "200", "0")) {
            throw SiteException("分片已传完，但入库失败：${raw.optString("msg", raw.optString("message"))}")
        }
        val node = raw.optJSONObject("msg") ?: JSONObject()
        return node.optString("mid") to node.optString("retpath")
    }

    /** 上传入库后自动审核：status=2 通过（与网页端「审核」按钮同一接口）。 */
    fun auditVoluntaryBook(mid: String): Pair<Boolean, String> {
        val form = FormBody.Builder().add("mid", mid).add("status", "2").build()
        val response = call(
            baseRequest("${SiteConstants.API_BASE_URL}/members/bg/auditVoluntaryBook")
                .post(form)
                .build()
        )
        val raw = runCatching { jsonOf(response) }.getOrElse { return false to "审核请求失败：${it.message}" }
        return if (raw.optString("status") == "OK") true to (raw.optString("msg").ifBlank { "审核通过" })
        else false to (raw.optString("msg").ifBlank { "审核未通过" })
    }

    /**
     * 手工拼 multipart：站点这一接口只认顺序分片，字段固定，
     * 用 OkHttp 的 MultipartBody 拼会被它改写掉 chunk 顺序语义。
     */
    private class MultipartBody(
        private val boundary: String,
        private val parts: MutableList<ByteArray>,
    ) {
        fun toRequestBody(): okhttp3.RequestBody {
            val tail = "--$boundary--\r\n".toByteArray(Charsets.US_ASCII)
            val bytes = parts.fold(ByteArray(0)) { acc, part -> acc + part } + tail
            return bytes.toRequestBody("multipart/form-data; boundary=$boundary".toMediaType())
        }

        class Builder {
            private val boundary = "----SmartYouthBoundary${System.currentTimeMillis()}"
            private val parts = ArrayList<ByteArray>()

            fun addFormDataPart(name: String, filename: String? = null, content: ByteArray? = null, value: String? = null) = apply {
                val head = StringBuilder("--$boundary\r\n")
                    .append("Content-Disposition: form-data; name=\"").append(name).append('"')
                if (filename != null && content != null) {
                    head.append("; filename=\"").append(filename).append("\"\r\n")
                        .append("Content-Type: application/octet-stream\r\n\r\n")
                    parts += head.toString().toByteArray(Charsets.US_ASCII)
                    parts += content
                    parts += "\r\n".toByteArray(Charsets.US_ASCII)
                } else {
                    head.append("\r\n\r\n").append(value.orEmpty()).append("\r\n")
                    parts += head.toString().toByteArray(Charsets.US_ASCII)
                }
            }

            fun build(): MultipartBody = MultipartBody(boundary, parts)
        }
    }
}

data class LoginVerdict(val kind: String, val message: String)

object MessageParse {
    private val leadPrefix = Regex("^\\s*[【\\[]([^】\\]]*)[】\\]]\\s*")
    private val wrap = Regex("^\\s*[【\\[]([^】\\]]*)[】\\]]\\s*$")
    private val tag = Regex("<[^>]+>")
    private val mobile = Regex("1\\d{10}")

    fun cleanTitle(title: String): String {
        var t = title.trim()
        leadPrefix.find(t)?.let { m ->
            if (m.groupValues[1].trim() == "系统通知") t = t.substring(m.range.last + 1)
        }
        wrap.matchEntire(t)?.let { t = it.groupValues[1] }
        return t.trim()
    }

    fun classify(title: String, content: String): String {
        val text = "$title $content"
        return when {
            listOf("转接", "转出", "转入").any { it in text } -> "transfer"
            listOf("报到", "报道").any { it in text } -> "report"
            else -> "other"
        }
    }

    fun extractMobile(content: String): String {
        Regex("data-mobile=['\"](\\d+)['\"]").find(content)?.let { return it.groupValues[1] }
        return mobile.find(stripHtml(content))?.value.orEmpty()
    }

    fun stripHtml(content: String): String = tag.replace(content, " ").trim()
}
