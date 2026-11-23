package com.smallylc.smartyouth.update

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.util.Log
import androidx.core.content.FileProvider
import kotlinx.coroutines.CancellationException
import com.smallylc.smartyouth.BuildConfig
import com.smallylc.smartyouth.app
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * GitHub 更新检查与下载，移植桌面端 app/services/updater.py。
 *
 * 与桌面端相同的两条原则：
 * - 检查只走直连：api.github.com 响应快，不让不可用的镜像拖住启动后的检查；
 * - 下载才用镜像：gh-proxy 型前缀拼接原始链接，并发测速按延迟排队，逐站回退。
 *
 * 手机版和桌面版共用同一个 Release：从所有 Release 的 APK 资产里按文件名解析
 * 手机版本号，取最高的一份。老版 `mobile-v*` Release 继续识别，统一 tag 里
 * 的 `SmartYouth-mobile-<版本>.apk` 也能收到更新。
 */
object GitHubUpdate {

    private const val TAG = "GitHubUpdate"
    const val REPO = "IsSmallPigPig/Smart-YLC"
    private const val RELEASES_API = "https://api.github.com/repos/$REPO/releases"
    // 兼容老版 mobile-v2.1.0 的资产名，也认统一 tag 里的 SmartYouth-mobile-2.2.0.apk
    private val APK_VERSION = Regex("""SmartYouth-mobile-v?(\d+\.\d+\.\d+)\.apk""", RegexOption.IGNORE_CASE)

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
        val sha256: String = "",   // GitHub asset 的 digest；镜像返回的内容要靠它验真伪
    )

    sealed class CheckResult {
        data object UpToDate : CheckResult()
        data class NewVersion(val info: Info) : CheckResult()
        data class Failed(val reason: String) : CheckResult()
    }

    /** 从 APK 资产名取手机版本号；老资产带 v、新资产不带，都要认。 */
    internal fun apkVersion(assetName: String): String? =
        APK_VERSION.find(assetName)?.groupValues?.get(1)

    /** 版本号点分数字比较；调用方传入的已经是纯数字版本。 */
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
                // 桌面版和手机版共用同一个 releases 列表，取宽一点，
                // 免得以后攒够桌面版本把 mobile-v* 挤出窗口后静默显示「已是最新」。
                .url("$RELEASES_API?per_page=100")
                .header("Accept", "application/vnd.github+json")
                .build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    return@use CheckResult.Failed("GitHub 返回 ${response.code}")
                }
                val body = response.body?.string().orEmpty()
                val releases = org.json.JSONArray(body)
                var latest: Info? = null
                for (i in 0 until releases.length()) {
                    val release = releases.getJSONObject(i)
                    if (release.optBoolean("draft") || release.optBoolean("prerelease")) continue
                    val notes = release.optString("body").take(2000)
                    val assets = release.optJSONArray("assets") ?: org.json.JSONArray()
                    for (j in 0 until assets.length()) {
                        val asset = assets.getJSONObject(j)
                        val name = asset.optString("name")
                        val version = apkVersion(name) ?: continue
                        val apkUrl = asset.optString("browser_download_url")
                        if (apkUrl.isEmpty()) continue
                        val candidate = Info(
                            version = version,
                            notes = notes,
                            apkUrl = apkUrl,
                            size = asset.optLong("size"),
                            // GitHub 给的是 "sha256:<hex>"；摘要随下载字节校验，
                            // 挡住镜像站返回篡改包（系统签名校验之外的第二道）。
                            sha256 = asset.optString("digest").removePrefix("sha256:"),
                        )
                        val current = latest
                        if (current == null || isNewer(version, current.version)) latest = candidate
                    }
                }
                val found = latest
                when {
                    found == null -> CheckResult.Failed("还没发布过手机版")
                    !isNewer(found.version, BuildConfig.VERSION_NAME) -> CheckResult.UpToDate
                    else -> CheckResult.NewVersion(found)
                }
            }
        } catch (e: CancellationException) {
            throw e   // 协程取消不是故障，别折叠成 Failed
        } catch (t: Throwable) {
            // 保留根因到日志：JSON 结构变了、SSL 失败、DNS 不通都压成一句
            // "网络不通"时，用户截图过来根本无从判断是哪一种。
            Log.w(TAG, "检查更新失败", t)
            CheckResult.Failed(t.message?.take(200) ?: "网络不通")
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
        // 21 站全并发会一次压满移动网络，且 awaitAll 要等最慢一站的 15s 读超时。
        // 限到 5 路；探测用短超时客户端，避免"连上不回包"的站拖住整批。
        val gate = Semaphore(5)
        MIRROR_PREFIXES.map { prefix ->
            async(Dispatchers.IO) {
                gate.withPermit {
                    val started = System.currentTimeMillis()
                    val ok = runCatching {
                        val request = okhttp3.Request.Builder()
                            .url(prefix + probe)
                            .header("Accept", "application/vnd.github+json")
                            .build()
                        probeClient.newCall(request).execute().use { r ->
                            // 读掉响应体，连接才能回到共享池给后续下载复用。
                            r.body?.string()
                            r.code == 200 || r.code == 403 || r.code == 429
                        }
                    }.getOrDefault(false)
                    val elapsed = System.currentTimeMillis() - started
                    if (ok && elapsed < 6000) prefix to elapsed else null
                }
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
        if (target.exists() && target.length() == info.size && digestOk(target, info)) {
            onProgress(100)
            return@withContext Result.success(uriOf(target))
        }
        val errors = ArrayList<String>()
        for (prefix in gateways) {
            var lastPercent = -1     // 每个网关从 0 重新报，否则重试时进度条从 50% 直接跳 51%
            val part = File(dir, target.name + ".part")
            runCatching {
                val request = okhttp3.Request.Builder().url(prefix + info.apkUrl).build()
                downloadClient.newCall(request).execute().use { response ->
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
                    if (!digestOk(part, info)) error("校验失败：下载内容与官方摘要不符")
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

    /** 探测用短超时：镜像"连上不回包"时不能拖住整批。 */
    private val probeClient by lazy {
        app.okhttpClient.newBuilder()
            .connectTimeout(4, TimeUnit.SECONDS)
            .readTimeout(3, TimeUnit.SECONDS)
            .build()
    }

    /** 下载用更长的读超时：APK 比 API 响应大得多。单例复用连接池，别在重试循环里反复建。 */
    private val downloadClient by lazy {
        app.okhttpClient.newBuilder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(90, TimeUnit.SECONDS)
            .build()
    }

    /** 用 GitHub 给的摘要校验下载字节；镜像站被篡改时这一关会挡下。 */
    internal fun sha256(file: File): String =
        MessageDigest.getInstance("SHA-256").let { md ->
            file.inputStream().use { input ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val n = input.read(buffer)
                    if (n < 0) break
                    md.update(buffer, 0, n)
                }
            }
            md.digest().joinToString("") { "%02x".format(it) }
        }

    /** 没有摘要（老 Release 未带 digest）时退回按大小校验，不阻碍升级。 */
    internal fun digestOk(file: File, info: Info): Boolean {
        if (info.sha256.isNotBlank()) return sha256(file).equals(info.sha256, ignoreCase = true)
        return info.size <= 0 || file.length() == info.size
    }

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
