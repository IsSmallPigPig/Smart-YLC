package com.smallylc.smartyouth.site

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 团员字段解析：站点不同版本字段名不一致，按别名表取第一个非空值，
 * 再把整数枚举翻成网页端同款中文（对照 league_member.js）。
 */
object MemberParser {

    private val aliases: Map<String, List<String>> = mapOf(
        "mid" to listOf("mid", "memberId", "id", "userId"),
        "name" to listOf("name", "memberName", "userName"),
        "mobile" to listOf("mobile", "phone", "phoneNo", "tel"),
        "realNameStatus" to listOf("isRealName", "realNameStatus", "authenticationStatus"),
        "reportStatus" to listOf("auditStatus", "reportDataStatus"),
        "ebookStatus" to listOf("volunteerBookModifyStatus", "ebookStatus"),
        "ecardStatus" to listOf("electronicHeadPictureStatus", "ecardPhotoStatus"),
        "unpaidTotal" to listOf("fees", "unpaidTotal", "notPayTotal", "未交团费总额（元）", "未交团费总额"),
        "unpaidMonths" to listOf("feesMonth", "unpaidMonths", "累计未交月份"),
        "oid" to listOf("oid", "orgId", "leagueId"),
        "orgName" to listOf("fullName", "orgName", "leagueName"),
        "joinDate" to listOf("leagueForYears", "joinDate", "入团时间"),
        "reportApplyAt" to listOf("createTimeStr", "reportApplyAt"),
    )

    private val realNameNames = mapOf(
        "10" to "通过", "1" to "校验中", "2" to "校验失败",
        "3" to "线下验证通过", "4" to "校验失败",
    )

    private val auditNames = mapOf(
        "0" to "报到被撤回", "1" to "报到待审核", "2" to "报到被退回", "3" to "审核通过",
        "4" to "修改资料待审核", "5" to "修改资料被退回",
        "6" to "已报到,电子志愿书未上传", "7" to "电子志愿书待上传", "8" to "待省级管理员审核",
    )

    private val ebookNames = mapOf(
        "0" to "-", "1" to "不通过", "2" to "通过", "3" to "修改待审核", "4" to "未上传", "5" to "已上传",
    )

    private val ecardNames = mapOf(
        "0" to "上传待审核", "1" to "修改待审核", "2" to "审核通过", "3" to "审核不通过",
    )

    fun parse(row: JSONObject, fallbackOid: String = ""): Member {
        fun raw(key: String): String {
            val keys = aliases[key] ?: return ""
            for (k in keys) {
                if (row.has(k) && !row.isNull(k)) {
                    val v = row.opt(k)
                    if (v != null && v.toString().isNotEmpty() && v.toString() != "null") return v.toString()
                }
            }
            // 站点字段名大小写不固定，兜底再按小写比一次
            val lower = row.keys().asSequence().associateBy { it.lowercase() }
            for (k in keys) {
                val actual = lower[k.lowercase()] ?: continue
                val v = row.opt(actual)
                if (v != null && v.toString().isNotEmpty()) return v.toString()
            }
            return ""
        }

        fun enumOf(key: String, table: Map<String, String>): String {
            val value = raw(key)
            return table[value] ?: value
        }

        val reportRaw = raw("reportStatus")
        val ebook = enumOf("ebookStatus", ebookNames).let { value ->
            if (value.isEmpty()) {
                // 状态为空时按报到情况派生，和网页端显示一致
                when (reportRaw) {
                    "6", "7" -> "未上传"
                    else -> if (row.opt("volunteerBook") != null) "已上传" else "未上传"
                }
            } else value
        }

        return Member(
            mid = raw("mid"),
            name = raw("name"),
            mobile = raw("mobile"),
            realNameStatus = enumOf("realNameStatus", realNameNames),
            reportStatus = auditNames[reportRaw] ?: reportRaw,
            ebookStatus = ebook,
            ecardStatus = enumOf("ecardStatus", ecardNames).ifEmpty { "未上传" },
            unpaidTotal = raw("unpaidTotal").toDoubleOrNull() ?: 0.0,
            unpaidMonths = raw("unpaidMonths").substringBefore('.').toIntOrNull() ?: 0,
            oid = raw("oid").ifEmpty { fallbackOid },
            orgName = raw("orgName"),
            joinDate = msToDate(raw("joinDate")),
            reportApplyAt = raw("reportApplyAt"),
        )
    }

    fun parseRows(array: JSONArray, fallbackOid: String = ""): List<Member> =
        (0 until array.length()).mapNotNull { i ->
            array.optJSONObject(i)?.let { runCatching { parse(it, fallbackOid) }.getOrNull() }
        }

    private fun msToDate(value: String): String {
        val ms = value.toLongOrNull() ?: return value
        return runCatching {
            SimpleDateFormat("yyyy-MM-dd", Locale.CHINA).format(Date(ms))
        }.getOrDefault(value)
    }

    /** 站点把列表塞在不同键里，逐个试。 */
    fun pickRows(raw: JSONObject): Pair<JSONArray, Int> {
        val candidates = listOf("data", "msg", "page", "result")
        for (key in candidates) {
            val node = raw.opt(key)
            val array = when (node) {
                is JSONArray -> node
                is JSONObject -> node.optJSONArray("list")
                    ?: node.optJSONArray("records")
                    ?: node.optJSONArray("rows")
                    ?: node.optJSONArray("listData")
                else -> null
            }
            if (array != null) {
                val total = when (node) {
                    is JSONObject -> node.optInt("total", node.optInt("count", array.length()))
                    else -> array.length()
                }
                return array to total
            }
        }
        return JSONArray() to 0
    }
}
