package com.smallylc.smartyouth.scan

import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 取景期的实时纸张识别。
 *
 * 每帧都做全量检测会把中端机打满，所以限到约 4 帧每秒：够跟手，又不会烫。
 * 识别到就把该区域透视校正成平铺图，交给取景框显示——用户看到的就是「拍下来会是什么样」。
 *
 * 校正结果是 Bitmap，会被 Compose 直接绘制。换新图时不能立刻回收旧图，
 * 因为上一帧可能还在渲染队列里；延迟 600ms 再回收。
 */
object LiveScanner {

    /** 两次识别的最小间隔，跟手程度与耗电的折中。 */
    private const val INTERVAL_MS = 240L

    private val _quad = MutableStateFlow<DocDetect.Quad?>(null)
    val quad: StateFlow<DocDetect.Quad?> = _quad.asStateFlow()

    private val _flat = MutableStateFlow<Bitmap?>(null)
    val flat: StateFlow<Bitmap?> = _flat.asStateFlow()

    private val main = Handler(Looper.getMainLooper())
    private var lastAt = 0L
    private var working = false
    private var filter = DocDetect.Filter.ENHANCE

    fun setFilter(next: DocDetect.Filter) {
        filter = next
    }

    fun reset() {
        _quad.value = null
        _flat.value = null
    }

    /** 由相机分析线程调用；负责回收 frame。 */
    fun onFrame(frame: Bitmap) {
        val now = SystemClock.elapsedRealtime()
        if (working || now - lastAt < INTERVAL_MS) {
            frame.recycle()
            return
        }
        lastAt = now
        working = true
        try {
            val small = DocDetect.downscale(frame, 900)
            val found = runCatching { DocDetect.detect(small) }.getOrNull()
            val output = found?.let { runCatching { DocDetect.rectify(small, it, filter) }.getOrNull() }
            if (small !== frame) small.recycle()
            if (found == null || output == null) {
                output?.recycle()
            } else {
                val previous = _flat.value
                _flat.value = output
                if (previous != null && previous !== output) {
                    main.postDelayed({ runCatching { previous.recycle() } }, 600)
                }
            }
            _quad.value = found
        } finally {
            frame.recycle()
            working = false
        }
    }
}
