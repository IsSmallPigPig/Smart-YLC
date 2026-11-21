package com.smallylc.smartyouth.update

import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.content.FileProvider
import com.smallylc.smartyouth.BuildConfig
import com.smallylc.smartyouth.app
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * GitHub 更新检查与下载，移植桌面端 app/services/updater.py。
 *
 * 与桌面端相同的两条原则：
 * - 检查只走直连：api.github.com 响应快，不让不可用的镜像拖住启动后的检查；
 * - 下载才用镜像：gh-proxy 型前缀拼接原始链接，并发测速按延迟排队，逐站回退。
 *
 * 手机版与桌面版同号不同线：桌面用仓库最新 Release，手机只认 tag 前缀
 * `mobile-v` 的 Release，APK 作为该 Release 的资产上传。
 */
object GitHubUpdate {

    const val REPO = "IsSmallPigPig/Smart-YLC"
    private const val RELEASES_API = "https://api.github.com/repos/$REPO/releases"
    private const val TAG_PREFIX = "mobile-v"

    /** 与桌面端同一份镜像清单（2026-11 核对）；检查走直连，这里只服务下载。 */
    private val MIRROR_PREFIXES = listOf(
        "https://gh-proxy.com/",
        "https://gh-proxy.org/",
        "https://cdn.gh-proxy.org/",
        "https://edgeone.gh-proxy.org/",
        "https://hk.gh-proxy.org/",
        "https://ghfast.top/",
        "https://ghfile.geekertao.top/",
        "https://gh.chjina.com/",
        "https://gh.monlor.com/",
        "https://gh.jasonzeng.dev/",
        "https://ghproxy.monkeyray.net/",
        "https://github.ednovas.xyz/",
        "https://gh.nxnow.top/",
        "https://ghproxy.cxkpro.top/",
        "https://fastgit.cc/",
        "https://gh.zwy.one/",
        "https://gitproxy.mrhjx.cn/",
        "https://github.boki.moe/",
        "https://gh.xxooo.cf/",
        "https://gh.llkk.cc/",
        "https://wget.la/",
    )

    data class Info(
        val version: String,
        val notes: String,
        val apkUrl: String,
        val size: Long,
    )

    sealed class CheckResult {
        data object UpToDate : CheckResult()
        data class NewVersion(val info: Info) : CheckResult()
        data class Failed(val reason: String) : CheckResult()
    }

    /** 版本号点分数字比较；tag 里的 `mobile-v` 前缀先剥掉。 */
    internal fun isNewer(remote: String, local: String): Boolean {
        fun parts(v: String) = Regex("\\d+").findAll(v).map { it.value.toLong() }.toList()
        val a = parts(remote)
        val b = parts(local)
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0L }
            val y = b.getOrElse(i) { 0L }
            if (x != y) return x > y
        }
        return false
    }

    private val client by lazy {
        app.okhttpClient.newBuilder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .build()
    }

    /** 检查手机版最新 Release。返回 null 表示仓库还没有发过手机版。 */
    suspend fun check(): CheckResult = withContext(Dispatchers.IO) {
        try {
            val request = okhttp3.Request.Builder()
                .url("$RELEASES_API?per_page=20")
                .header("Accept", "application/vnd.github+json")
                .build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    return@use CheckResult.Failed("GitHub 返回 ${response.code}")
                }
                val body = response.body?.string().orEmpty()
                val releases = org.json.JSONArray(body)
                var tagVersion: String? = null
                var notes = ""
                var apkUrl = ""
                var apkSize = 0L
                for (i in 0 until releases.length()) {
                    val release = releases.getJSONObject(i)
                    val tag = release.optString("tag_name")
                    if (!tag.startsWith(TAG_PREFIX) || release.optBoolean("draft") || release.optBoolean("prerelease")) continue
                    tagVersion = tag.removePrefix(TAG_PREFIX)
                    notes = release.optString("body").take(2000)
                    val assets = release.optJSONArray("assets") ?: org.json.JSONArray()
                    for (j in 0 until assets.length()) {
                        val asset = assets.getJSONObject(j)
                        if (asset.optString("name").endsWith(".apk")) {
                            apkUrl = asset.optString("browser_download_url")
                            apkSize = asset.optLong("size")
                            break
                        }
                    }
                    break
                }
                val version = tagVersion
                if (version == null || apkUrl.isEmpty()) {
                    CheckResult.Failed("还没发布过手机版")
                } else if (!isNewer(version, BuildConfig.VERSION_NAME)) {
                    CheckResult.UpToDate
                } else {
                    CheckResult.NewVersion(Info(version, notes, apkUrl, apkSize))
                }
            }
        } catch (t: Throwable) {
            CheckResult.Failed(t.message ?: "网络不通")
        }
    }

    // 探测结果缓存十分钟，和桌面端 PROBE_TTL 一致；每次下载都全量探测 21 站太重。
    @Volatile
    private var rankedCache: Pair<Long, List<String>>? = null

    /**
     * 测速并按延迟升序返回可用前缀（直连垫底）。
     * 探测文件用 Release 的 API 本身：小、且能验证内容真的到达。
     */
    private suspend fun rankedPrefixes(): List<String> {
        val cached = rankedCache
        if (cached != null && System.currentTimeMillis() - cached.first < 10 * 60 * 1000L) {
            return cached.second
        }
        val ranked = probePrefixes()
        if (ranked.isNotEmpty()) rankedCache = System.currentTimeMillis() to ranked
        return ranked
    }

    private suspend fun probePrefixes(): List<String> = coroutineScope {
        val probe = "$RELEASES_API?per_page=1"
        MIRROR_PREFIXES.map { prefix ->
            async(Dispatchers.IO) {
                val started = System.currentTimeMillis()
                val ok = runCatching {
                    val request = okhttp3.Request.Builder()
                        .url(prefix + probe)
                        .header("Accept", "application/vnd.github+json")
                        .build()
                    client.newCall(request).execute().use { r ->
                        // 读掉响应体，连接才能回到共享池给后续下载复用。
                        r.body?.string()
                        r.code == 200 || r.code == 403 || r.code == 429
                    }
                }.getOrDefault(false)
                val elapsed = System.currentTimeMillis() - started
                if (ok && elapsed < 6000) prefix to elapsed else null
            }
        }.awaitAll().filterNotNull().sortedBy { it.second }.map { it.first }
    }

    /**
     * 下载 APK 到缓存目录，返回可安装的 Uri。
     * 逐站尝试（先测速排名，再直连），`.part` 落地后原子改名。
     */
    suspend fun download(
        info: Info,
        onProgress: (Int) -> Unit,
    ): Result<Uri> = withContext(Dispatchers.IO) {
        val ranked = rankedPrefixes()
        val gateways = ranked + ""
        val dir = File(app.cacheDir, "update").apply { mkdirs() }
        val target = File(dir, "smart-youth-${info.version}.apk")
        if (target.exists() && target.length() == info.size) {
            onProgress(100)
            return@withContext Result.success(uriOf(target))
        }
        val errors = ArrayList<String>()
        var lastPercent = -1
        for (prefix in gateways) {
            val part = File(dir, target.name + ".part")
            runCatching {
                val request = okhttp3.Request.Builder().url(prefix + info.apkUrl).build()
                downloadClient().newCall(request).execute().use { response ->
                    if (!response.isSuccessful) error("HTTP ${response.code}")
                    val total = if (info.size > 0) info.size else response.body?.contentLength() ?: -1L
                    part.outputStream().use { out ->
                        response.body!!.byteStream().use { input ->
                            val buffer = ByteArray(64 * 1024)
                            var done = 0L
                            while (true) {
                                val read = input.read(buffer)
                                if (read < 0) break
                                out.write(buffer, 0, read)
                                done += read
                                if (total > 0) {
                                    val percent = ((done * 100) / total).toInt().coerceAtMost(99)
                                    if (percent != lastPercent) {
                                        lastPercent = percent
                                        onProgress(percent)
                                    }
                                }
                            }
                        }
                    }
                    if (total > 0 && part.length() != total) error("下载不完整")
                    target.delete()
                    if (!part.renameTo(target)) error("保存失败")
                }
            }.onSuccess {
                onProgress(100)
                return@withContext Result.success(uriOf(target))
            }.onFailure {
                part.delete()
                errors += "${prefix.removeSuffix("/")
                    .ifEmpty { "直连" }}: ${it.message}"
            }
        }
        Result.failure(IllegalStateException(errors.lastOrNull() ?: "下载失败"))
    }

    /** 下载用更长的读超时：APK 比 API 响应大得多。 */
    private fun downloadClient() = app.okhttpClient.newBuilder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .build()

    private fun uriOf(file: File): Uri =
        FileProvider.getUriForFile(app, "${app.packageName}.fileprovider", file)

    /** 拉起系统安装器。需要 REQUEST_INSTALL_PACKAGES，未授权时系统会引导到设置页。 */
    fun installIntent(uri: Uri): Intent = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(uri, "application/vnd.android.package-archive")
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    }

    fun canInstall(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.O ||
            app.packageManager.canRequestPackageInstalls()
}
