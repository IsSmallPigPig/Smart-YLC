package com.smallylc.smartyouth.site

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 绑定信息要在会话失效后仍保留设备名，档案页才能说清断的是哪台电脑。 */
class PairBindingTest {

    private fun binding(token: String = "tok") = PairBinding(
        base = "http://192.168.1.9:8765",
        pin = "ABC123",
        token = token,
        device = "我的电脑",
        configCode = "ABC234",
        pairedAt = 1000L,
    )

    @Test
    fun hostStripsSchemeAndPort() {
        assertEquals("192.168.1.9", binding().host)
    }

    @Test
    fun usableRequiresHttpAndToken() {
        assertTrue(binding().usable)
        assertFalse(binding(token = "").usable)
        assertFalse(binding().copy(base = "ftp://x").usable)
    }

    @Test
    fun expireKeepsDeviceButDropsToken() {
        val expired = PairBinding.expire(binding())
        assertEquals("我的电脑", expired.device)
        assertEquals("", expired.token)
        assertFalse(expired.usable)
    }

    @Test
    fun summaryTellsWhichDeviceIsConnected() {
        assertTrue(PairBinding.summary(null).contains("未连接"))
        assertTrue(PairBinding.summary(binding()).contains("192.168.1.9"))
        assertTrue(PairBinding.summary(PairBinding.expire(binding())).contains("断开"))
    }
}
