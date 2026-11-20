package com.smallylc.smartyouth.site

import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/** 同一 Wi-Fi 下搜到的电脑。字段与扫码得到的信息同构，可直接交给 [PairClient]。 */
data class DiscoveredPeer(
    val name: String,
    val host: String,
    val port: Int,
    val pin: String,
) {
    val base: String get() = "http://$host:$port"

    fun client(configCode: String = "") = PairClient(base, pin.uppercase(), configCode)
}

/**
 * 局域网自动发现。
 *
 * 电脑端在 8766 端口应答 `SMARTYOUTH/1` 探测包，回一条 JSON：设备名、HTTP 端口、
 * 配对码。手机广播一次、收一秒多即可，不起常驻组播——组播在不少路由器上被过滤，
 * 而且常驻监听费电。
 *
 * 配对码随应答带回，是因为它本来就在电脑屏幕的二维码里明文显示；参与云端加密的
 * 提取码不在这条广播里，仍需用户自己填。
 */
object LanDiscovery {
    const val PORT = 8766
    private val MAGIC = "SMARTYOUTH/1".toByteArray()

    fun scan(timeoutMs: Long = 1200): List<DiscoveredPeer> {
        val found = LinkedHashMap<String, DiscoveredPeer>()
        runCatching {
            DatagramSocket().use { socket ->
                socket.broadcast = true
                socket.soTimeout = 200
                socket.send(
                    DatagramPacket(MAGIC, MAGIC.size, InetAddress.getByName("255.255.255.255"), PORT)
                )
                val buf = ByteArray(512)
                val deadline = System.currentTimeMillis() + timeoutMs
                while (System.currentTimeMillis() < deadline) {
                    val packet = DatagramPacket(buf, buf.size)
                    val arrived = runCatching { socket.receive(packet) }.getOrNull() ?: continue
                    val text = String(arrived.data, 0, arrived.length)
                    val obj = runCatching { JSONObject(text) }.getOrNull() ?: continue
                    if (obj.optString("service") != "smartyouth") continue
                    val host = arrived.address?.hostAddress ?: continue
                    val pin = obj.optString("pin")
                    if (pin.isBlank()) continue
                    found[host] = DiscoveredPeer(
                        name = obj.optString("name").ifBlank { host },
                        host = host,
                        port = obj.optInt("port", 8765),
                        pin = pin,
                    )
                }
            }
        }
        return found.values.toList()
    }
}
