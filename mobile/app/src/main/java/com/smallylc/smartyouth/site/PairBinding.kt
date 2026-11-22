package com.smallylc.smartyouth.site

/**
 * 手机与电脑的绑定信息。配对成功后落盘，退出「传到电脑」页仍然保留。
 *
 * token 只在电脑端本次接收服务存活期间有效。电脑关掉弹窗后服务停止，
 * token 会失效；[needsRepair] 用来判断要不要重新配对，而不是默默重传失败。
 */
data class PairBinding(
    val base: String,
    val pin: String,
    val token: String,
    val device: String,
    val configCode: String,
    val pairedAt: Long,
) {
    val host: String get() = base.removePrefix("http://").substringBefore(":")

    /** 空凭证不可用，读盘时按无效绑定处理。 */
    val usable: Boolean get() = base.startsWith("http") && token.isNotBlank()

    companion object {
        /** 会话失效时保留设备名，只清 token，界面还能显示上次连的是哪台。 */
        fun expire(binding: PairBinding): PairBinding = binding.copy(token = "")

        /** 档案页的一行连接信息，长词拆行，不拿中点拼字符串。 */
        fun summary(binding: PairBinding?): String = when {
            binding == null -> "未连接电脑"
            binding.token.isBlank() -> "${binding.device} 连接已断开"
            else -> "${binding.device} 已在 ${binding.host}"
        }
    }
}
