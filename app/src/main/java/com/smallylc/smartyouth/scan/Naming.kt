package com.smallylc.smartyouth.scan

import com.smallylc.smartyouth.site.Member
import com.smallylc.smartyouth.site.SiteConstants

/**
 * 站点只认 `姓名+手机号+入团志愿书.pdf` 这一种命名。
 * 这里负责从团员记录生成文件名，也在用户手输时把问题指出来 —— 不改用户输入。
 */
object Naming {

    private val illegal = Regex("[\\\\/:*?\"<>|\\u0000-\\u001f]")
    private val digit = Regex("\\d")
    private val space = Regex("[\\s\\u3000]")

    fun fileNameFor(member: Member): String =
        "${member.name}+${member.mobile}+${SiteConstants.NAME_SUFFIX}.pdf"

    /** 用户先输了姓名但还没选团员时，给一个能直接用的猜测。 */
    fun guessFrom(input: String, members: List<Member>): String? {
        val text = input.trim()
        if (text.isEmpty()) return null
        val exact = members.firstOrNull { it.name == text && it.mobile.isNotEmpty() }
        if (exact != null) return fileNameFor(exact)
        val mobileInText = Regex("1[3-9]\\d{9}").find(text)?.value
        if (mobileInText != null) {
            val byMobile = members.firstOrNull { it.mobile == mobileInText }
            if (byMobile != null) return fileNameFor(byMobile)
            val name = text.replace(mobileInText, "").trim('+', '-', ' ')
            if (name.isNotEmpty()) {
                return "$name+$mobileInText+${SiteConstants.NAME_SUFFIX}.pdf"
            }
        }
        return null
    }

    data class Check(val ok: Boolean, val hint: String)

    fun check(fileName: String): Check {
        val name = fileName.trim()
        if (name.isEmpty()) return Check(false, "先填姓名，或从团员列表里选一个")
        val base = name.removeSuffix(".pdf").removeSuffix(".PDF")

        val problems = ArrayList<String>()
        if (!SiteConstants.isPdf(name)) problems += "需要 .pdf 结尾"
        if (illegal.containsMatchIn(base)) problems += "含非法字符"
        if (space.containsMatchIn(base)) problems += "不能有空格"
        if (base.contains('＋') || base.contains('－')) problems += "分隔符要用半角 +"

        val parts = base.split("+")
        if (parts.size != 3) {
            return Check(false, "按 姓名+手机号+入团志愿书 三段写")
        }
        val (person, mobile, suffix) = parts
        if (person.isBlank() || digit.containsMatchIn(person)) problems += "姓名为空或含数字"
        if (!SiteConstants.isMobile(mobile)) problems += "手机号不是 11 位"
        if (suffix != SiteConstants.NAME_SUFFIX) problems += "结尾应为「${SiteConstants.NAME_SUFFIX}」"

        return if (problems.isEmpty()) Check(true, "文件名可用")
        else Check(false, problems.joinToString("，"))
    }

    /** 下拉补全时展示的一行：姓名 + 手机号 + 支部。 */
    fun label(member: Member): String = buildString {
        append(member.name)
        if (member.mobile.isNotBlank()) append("  ").append(member.mobile)
        if (member.displayOrg.isNotBlank()) append("  ").append(member.displayOrg)
    }
}
