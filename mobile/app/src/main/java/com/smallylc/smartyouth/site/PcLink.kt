package com.smallylc.smartyouth.site

import com.smallylc.smartyouth.data.PairStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * 与电脑的长期传输通道。配对成功后不再调用 /api/done——那样会作废 token，
 * 下一张新拍的档案就传不过去了。会话由电脑端接收服务管理，关掉弹窗自然失效。
 *
 * 上传失败要给出明确原因：会话失效只标记断开，文件仍留在扫描件里；网络不通
 * 保留绑定，让用户下次还能重试，不把已配对的电脑悄悄忘掉。
 */
object PcLink {

    sealed interface Upload {
        data class Done(val text: String) : Upload
        data class Failed(val message: String) : Upload
    }

    suspend fun upload(file: File): Upload = withContext(Dispatchers.IO) {
        val b = PairStore.binding.value
        if (b == null || !b.usable) {
            return@withContext Upload.Failed("还没连接电脑，先到扫描页配对")
        }
        when (val result = PairClient(b.base, b.pin, b.configCode).upload(b.token, file)) {
            is PairClient.Result.Ok -> Upload.Done(result.value)
            is PairClient.Result.Fail -> when (result.kind) {
                "session" -> {
                    PairStore.expire()
                    Upload.Failed("电脑连接已断开，请重新配对")
                }
                "offline" -> Upload.Failed("连不上电脑，检查两边是否在同一 Wi-Fi")
                else -> Upload.Failed(result.message)
            }
        }
    }

    /** 主动解除绑定：通知电脑作废会话，再清本机记录。 */
    suspend fun disconnect() = withContext(Dispatchers.IO) {
        val b = PairStore.binding.value
        if (b != null && b.token.isNotBlank()) {
            PairClient(b.base, b.pin, b.configCode).done(b.token)
        }
        PairStore.clear()
    }
}
