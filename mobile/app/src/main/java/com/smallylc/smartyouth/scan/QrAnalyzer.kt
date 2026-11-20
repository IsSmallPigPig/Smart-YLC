package com.smallylc.smartyouth.scan

import android.os.SystemClock
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.HybridBinarizer

/**
 * 二维码识别：只认 QR，节流到每 300ms 一帧，避免连续解码把相机线程占满。
 * 亮度平面按 rowStride 裁掉行尾填充，否则宽高与数据长度不匹配会解不出来。
 */
class QrAnalyzer(private val onText: (String) -> Unit) : ImageAnalysis.Analyzer {

    private val reader = MultiFormatReader().apply {
        setHints(mapOf(DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE)))
    }

    private var lastAttempt = 0L

    override fun analyze(image: ImageProxy) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastAttempt < 300) {
            image.close()
            return
        }
        lastAttempt = now
        try {
            val plane = image.planes[0]
            val buffer = plane.buffer
            val width = image.width
            val height = image.height
            val rowStride = plane.rowStride
            val data = if (rowStride == width) {
                ByteArray(buffer.remaining()).also { buffer.get(it) }
            } else {
                val out = ByteArray(width * height)
                val row = ByteArray(rowStride)
                for (y in 0 until height) {
                    val take = minOf(rowStride, buffer.remaining())
                    if (take <= 0) break
                    buffer.get(row, 0, take)
                    System.arraycopy(row, 0, out, y * width, width)
                }
                out
            }
            val source = PlanarYUVLuminanceSource(data, width, height, 0, 0, width, height, false)
            val text = reader.decode(BinaryBitmap(HybridBinarizer(source))).text
            if (!text.isNullOrBlank()) onText(text)
        } catch (_: Throwable) {
            // 没对上焦/画面里没有码是常态，不抛给调用方
        } finally {
            image.close()
        }
    }
}
