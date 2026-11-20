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

    /** 相机分析线程调用；负责回收 frame。 */
    fun onFrame(frame: Bitmap, rotationDegrees: Int) {
        val now = SystemClock.elapsedRealtime()
        if (working || now - lastAt < INTERVAL_MS) {
            frame.recycle()
            return
        }
        lastAt = now
        working = true
        val upright = if (rotationDegrees % 360 == 0) {
            frame
        } else {
            runCatching {
                val m = Matrix().apply { postRotate(rotationDegrees.toFloat()) }
                Bitmap.createBitmap(frame, 0, 0, frame.width, frame.height, m, true)
            }.getOrNull() ?: frame
        }
        try {
            _aspect.value = upright.width.toFloat() / max(1, upright.height)
            val small = DocDetect.downscale(upright, 900)
            _quad.value = runCatching { DocDetect.detect(small) }.getOrNull()
            if (small !== upright) small.recycle()
        } finally {
            if (upright !== frame) upright.recycle()
            frame.recycle()
            working = false
        }
    }
}
