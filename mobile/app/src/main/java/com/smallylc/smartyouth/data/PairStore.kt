package com.smallylc.smartyouth.data

import android.content.Context
import androidx.core.content.edit
import com.smallylc.smartyouth.app
import com.smallylc.smartyouth.site.PairBinding
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** 电脑绑定：配对成功就落盘，退出页面、重启 App 都还在。 */
object PairStore {

    private val prefs by lazy { app.getSharedPreferences("pc_pair", Context.MODE_PRIVATE) }
    private val _binding = MutableStateFlow<PairBinding?>(null)
    val binding: StateFlow<PairBinding?> = _binding.asStateFlow()

    /** 启动时读一次；读到的凭证不完整就当没连过，不让坏数据带进上传链路。 */
    fun restore(): PairBinding? {
        val b = PairBinding(
            base = prefs.getString("base", "").orEmpty(),
            pin = prefs.getString("pin", "").orEmpty(),
            token = prefs.getString("token", "").orEmpty(),
            device = prefs.getString("device", "").orEmpty(),
            configCode = prefs.getString("configCode", "").orEmpty(),
            pairedAt = prefs.getLong("pairedAt", 0L),
        )
        val usable = b.takeIf { it.base.startsWith("http") && it.device.isNotBlank() }
        _binding.value = usable
        return usable
    }

    fun save(b: PairBinding) {
        prefs.edit {
            putString("base", b.base)
            putString("pin", b.pin)
            putString("token", b.token)
            putString("device", b.device)
            putString("configCode", b.configCode)
            putLong("pairedAt", b.pairedAt)
        }
        _binding.value = b
    }

    /** 会话失效时只清 token，保留设备名供档案页显示「连接已断开」。 */
    fun expire() {
        val b = _binding.value ?: return
        val next = PairBinding.expire(b)
        prefs.edit { putString("token", "") }
        _binding.value = next
    }

    fun clear() {
        prefs.edit { clear() }
        _binding.value = null
    }
}
