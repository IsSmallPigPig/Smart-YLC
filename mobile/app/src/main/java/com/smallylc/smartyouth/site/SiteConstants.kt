package com.smallylc.smartyouth.site

import java.security.MessageDigest

/** 站点固定的地址与协议常量（对照桌面端 constants.py / login_protocol.py）。 */
object SiteConstants {
    const val SITE_BASE_URL = "https://tuan.12355.net"
    const val API_BASE_URL = "https://tuanapi.12355.net"
    const val AUTHORIZE_HEADER = "authorize_name"
    const val USER_AGENT =
        "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/138.0.0.0 Mobile Safari/537.36"

    const val LOGIN_PATH = "/login/adminLogin"
    const val CAPTCHA_PATH = "/login/loginValidCode"
    const val CAPTCHA_HEADER = "POWERED-BY-MENGXIANHUI"

    const val NAME_SUFFIX = "入团志愿书"
    const val CHUNK_BYTES = 10 * 1024 * 1024
    const val SITE_SINGLE_FILE_BYTES = 10 * 1024 * 1024
    const val PDF_TARGET_BYTES = (9.4 * 1024 * 1024).toInt()

    private val STRONG_PASSWORD =
        Regex("^(?=.*\\d)(?=.*[a-zA-Z])(?=.*[^a-zA-Z0-9]).{8,}$")
    private val MOBILE = Regex("^1[3-9]\\d{9}$")

    fun isStrongPassword(plain: String): Boolean = STRONG_PASSWORD.matches(plain)

    fun isMobile(value: String): Boolean = MOBILE.matches(value)

    fun isPdf(name: String): Boolean = name.endsWith(".pdf", ignoreCase = true)

    fun md5(text: String): String =
        MessageDigest.getInstance("MD5")
            .digest(text.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

    /** 站点登录用 md5(md5(明文))，与网页端 Utils 一致。 */
    fun doubleMd5(plain: String): String = md5(md5(plain))
}
