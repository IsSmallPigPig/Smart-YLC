package com.smallylc.smartyouth.site

/** 站点返回的团员记录，字段名与桌面端 MemberRecord 对齐，方便两个端对照。 */
data class Member(
    val mid: String = "",
    val name: String = "",
    val mobile: String = "",
    val realNameStatus: String = "",
    val reportStatus: String = "",
    val ebookStatus: String = "",
    val ecardStatus: String = "",
    val unpaidTotal: Double = 0.0,
    val unpaidMonths: Int = 0,
    val oid: String = "",
    val orgName: String = "",
    val joinDate: String = "",
    val reportApplyAt: String = "",
) {
    val uploaded: Boolean get() = ebookStatus.contains("已上传")

    /** 报到情况：人已报到但缺志愿书档案，和「没有报到」要分开看。 */
    val archiveMissing: Boolean
        get() = reportStatus in setOf("电子志愿书待上传", "已报到,电子志愿书未上传")

    val reportOk: Boolean get() = reportStatus.contains("审核通过")

    val identityOk: Boolean get() = realNameStatus.contains("通过")

    val feesOk: Boolean get() = unpaidTotal <= 0.0 && unpaidMonths <= 0

    /** 首页四类异常计数用的分类键；无异常返回 null。 */
    fun firstProblem(): String? = when {
        !identityOk -> "identity"
        archiveMissing -> "archive"
        !reportOk -> "report"
        !feesOk -> "fees"
        else -> null
    }

    fun problems(): List<String> = buildList {
        if (!identityOk) add("实名未通过")
        if (archiveMissing) add("缺志愿书档案")
        else if (!reportOk) add("未报到")
        if (!feesOk) add(
            buildString {
                append("欠缴团费")
                if (unpaidTotal > 0) append(" ${trimAmount(unpaidTotal)} 元")
                if (unpaidMonths > 0) append(" ${unpaidMonths} 个月")
            }
        )
    }

    val abnormal: Boolean get() = firstProblem() != null

    val displayOrg: String
        get() = orgName.ifBlank { oid }
}

fun trimAmount(value: Double): String =
    if (value == value.toLong().toDouble()) value.toLong().toString() else String.format("%.2f", value)

data class OrgNode(
    val oid: String,
    val name: String,
    val parentOid: String = "",
)

data class SiteMessage(
    val id: String,
    val title: String,
    val content: String,
    val mobile: String,
    val kind: String,
    val status: Int,
    val timeStr: String,
) {
    val kindLabel: String
        get() = when (kind) {
            "report" -> "报到申请"
            "transfer" -> "转接申请"
            else -> "其他"
        }

    val unread: Boolean get() = status == 0
}

/** 首页概览：与桌面端 org_stats 同口径。 */
data class Overview(
    val total: Int = 0,
    val uploaded: Int = 0,
    val abnormal: Int = 0,
    val identity: Int = 0,
    val report: Int = 0,
    val archive: Int = 0,
    val fees: Int = 0,
    val unreadMessages: Int = 0,
) {
    val ratio: Float get() = if (total == 0) 0f else uploaded.toFloat() / total
}

fun List<Member>.toOverview(unread: Int = 0): Overview {
    var uploaded = 0
    var identity = 0
    var report = 0
    var archive = 0
    var fees = 0
    forEach { m ->
        if (m.uploaded) uploaded++
        if (!m.identityOk) identity++
        if (m.archiveMissing) archive++ else if (!m.reportOk) report++
        if (!m.feesOk) fees++
    }
    val abnormal = count { it.abnormal }
    return Overview(
        total = size,
        uploaded = uploaded,
        abnormal = abnormal,
        identity = identity,
        report = report,
        archive = archive,
        fees = fees,
        unreadMessages = unread,
    )
}
