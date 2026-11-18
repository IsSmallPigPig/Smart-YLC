package com.smallylc.smartyouth.scan

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import com.smallylc.smartyouth.app
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.atomic.AtomicLong

data class ScanPage(
    val id: Long,
    val file: File,
    val quad: DocDetect.Quad,
    val filter: DocDetect.Filter,
    val rotation: Int = 0,
)

/**
 * 一次扫描会话：拍照 → 每页落一个 JPEG → 四角可再调整 → 合成 PDF。
 *
 * 页面文件放 cacheDir，进程被杀也不丢（文件还在），重进扫描页会清掉上一轮残留。
 */
object ScanSession {

    private val counter = AtomicLong(System.currentTimeMillis())

    private val _pages = MutableStateFlow<List<ScanPage>>(emptyList())
    val pages: StateFlow<List<ScanPage>> = _pages.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    private val sessionDir: File
        get() = File(app.cacheDir, "scan/session").apply { mkdirs() }

    fun startFresh() {
        sessionDir.listFiles()?.forEach { it.delete() }
        _pages.value = emptyList()
    }

    fun clear() {
        sessionDir.listFiles()?.forEach { it.delete() }
        _pages.value = emptyList()
    }

    val isEmpty: Boolean get() = _pages.value.isEmpty()

    /** 相机拍好的一页：读图 → 自动找纸边 → 落盘。找不到纸边时给整幅留边，用户仍可手调。 */
    suspend fun addFromFile(source: File): ScanPage = withContext(Dispatchers.IO) {
        _busy.value = true
        try {
            val bitmap = decode(source, 2400)
            val quad = DocDetect.detect(bitmap) ?: DocDetect.inset()
            val id = counter.incrementAndGet()
            val target = File(sessionDir, "page_$id.jpg")
            target.outputStream().use { out ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 95, out)
            }
            bitmap.recycle()
            source.delete()
            val page = ScanPage(id, target, quad, DocDetect.Filter.ENHANCE)
            _pages.value = _pages.value + page
            page
        } finally {
            _busy.value = false
        }
    }

    suspend fun importFromUri(uri: android.net.Uri): ScanPage? = withContext(Dispatchers.IO) {
        val bitmap = runCatching {
            app.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it) }
        }.getOrNull() ?: return@withContext null
        val temp = File(sessionDir, "import_${counter.incrementAndGet()}.jpg")
        temp.outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 95, it) }
        bitmap.recycle()
        addFromFile(temp)
    }

    fun updateQuad(pageId: Long, quad: DocDetect.Quad) {
        _pages.value = _pages.value.map { if (it.id == pageId) it.copy(quad = quad) else it }
    }

    fun updateFilter(pageId: Long, filter: DocDetect.Filter) {
        _pages.value = _pages.value.map { if (it.id == pageId) it.copy(filter = filter) else it }
    }

    suspend fun rotate(pageId: Long) = withContext(Dispatchers.IO) {
        val page = _pages.value.firstOrNull { it.id == pageId } ?: return@withContext
        val bitmap = decode(page.file, 2400)
        val rotated = Bitmap.createBitmap(
            bitmap, 0, 0, bitmap.width, bitmap.height,
            Matrix().apply { postRotate(90f) }, true,
        )
        page.file.outputStream().use { rotated.compress(Bitmap.CompressFormat.JPEG, 95, it) }
        if (rotated !== bitmap) bitmap.recycle()
        rotated.recycle()
        // 图像转了 90°，四角跟着转，裁切框仍贴在纸上
        val rotatedQuad = DocDetect.Quad(page.quad.points.map { (x, y) -> (1f - y) to x })
        _pages.value = _pages.value.map {
            if (it.id == pageId) it.copy(quad = rotatedQuad, rotation = (it.rotation + 90) % 360) else it
        }
    }

    fun delete(pageId: Long) {
        val page = _pages.value.firstOrNull { it.id == pageId } ?: return
        page.file.delete()
        _pages.value = _pages.value.filterNot { it.id == pageId }
    }

    /** 预览与 PDF 用的校正图；maxEdge 小一点给缩略图，大一点给导出。 */
    suspend fun render(page: ScanPage, maxEdge: Int): Bitmap? = withContext(Dispatchers.IO) {
        runCatching {
            val source = decode(page.file, maxEdge)
            val result = DocDetect.rectify(source, page.quad, page.filter)
            val scaled = DocDetect.downscale(result, maxEdge)
            if (scaled !== result) result.recycle()
            if (source !== scaled) source.recycle()
            scaled
        }.getOrNull()
    }

    suspend fun buildPdf(target: File): PdfWriter.Result? = withContext(Dispatchers.IO) {
        val pages = _pages.value
        if (pages.isEmpty()) return@withContext null
        val bitmaps = pages.mapNotNull { render(it, 2200) }
        try {
            PdfWriter.write(bitmaps, target)
        } finally {
            bitmaps.forEach { it.recycle() }
        }
    }

    private fun decode(file: File, maxEdge: Int): Bitmap {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.absolutePath, bounds)
        var sample = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / sample > maxEdge * 2) sample *= 2
        val options = BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val bitmap = BitmapFactory.decodeFile(file.absolutePath, options)
            ?: throw IllegalStateException("图片读取失败")
        return DocDetect.downscale(bitmap, maxEdge)
    }
}
