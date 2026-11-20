package com.smallylc.smartyouth.scan

import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.SystemClock
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.math.max

/**
 * 取景期的实时纸张识别。
 *
 * 只做检测，不做校正：界面上要把「最终会裁下来的边界」用细线框实时画出来，
 * 所以这里输出的是与预览同向的归一化四角，而不是一张处理好的图。
 *
 * 每帧都全量检测会把中端机打满，限到约 4 帧每秒，够跟手也不烫。
 */
object LiveScanner {

    private const val INTERVAL_MS = 220L

    private val _quad = MutableStateFlow<DocDetect.Quad?>(null)
    val quad: StateFlow<DocDetect.Quad?> = _quad.asStateFlow()

    /** 旋转对齐预览后的画面宽高比，界面用它把归一化四角映射到屏幕。 */
    private val _aspect = MutableStateFlow(0f)
    val aspect: StateFlow<Float> = _aspect.asStateFlow()

    private var lastAt = 0L
    private var working = false

    fun reset() {
        _quad.value = null
    }

    /**
     * 传入的 bitmap 属于 PreviewView，不能回收；需要转换时自己 copy 一份。
     */
    fun onFrame(source: Bitmap, rotationDegrees: Int) {
        val now = SystemClock.elapsedRealtime()
        if (working || now - lastAt < INTERVAL_MS) return
        lastAt = now
        working = true
        var owned: Bitmap? = null
        try {
            // PreviewView 可能给 HARDWARE 位图，OpenCV 读不了，先转成软件位图
            val usable = if (source.config == Bitmap.Config.HARDWARE) {
                source.copy(Bitmap.Config.ARGB_8888, false)?.also { owned = it }
            } else {
                source
            } ?: return
            _aspect.value = usable.width.toFloat() / max(1, usable.height)
            val small = DocDetect.downscale(usable, 900)
            val found = try {
                DocDetect.detect(small)
            } catch (t: Throwable) {
                android.util.Log.w("LiveScanner", "detect failed", t)
                null
            }
            _quad.value = found
            if (small !== usable) small.recycle()
        } finally {
            owned?.recycle()
            working = false
        }
    }
}
