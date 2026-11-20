package com.smallylc.smartyouth.scan

import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import java.io.File

/** 读取已保存扫描件的页面，用于应用内预览。 */
object PdfPages {

    fun count(file: File): Int = runCatching {
        ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY).use { pfd ->
            PdfRenderer(pfd).use { it.pageCount }
        }
    }.getOrDefault(0)

    /** 渲染第 [index] 页，宽度按 [width] 等比缩放，白底。 */
    fun render(file: File, index: Int, width: Int): Bitmap? = runCatching {
        ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY).use { pfd ->
            PdfRenderer(pfd).use { renderer ->
                if (index !in 0 until renderer.pageCount) return@use null
                renderer.openPage(index).use { page ->
                    val scale = width.toFloat() / page.width
                    val height = (page.height * scale).toInt().coerceAtLeast(1)
                    Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).also { bitmap ->
                        bitmap.eraseColor(Color.WHITE)
                        page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                    }
                }
            }
        }
    }.getOrNull()
}
