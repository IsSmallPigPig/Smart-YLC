package com.smallylc.smartyouth.scan

import android.graphics.Bitmap
import com.smallylc.smartyouth.site.SiteConstants
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.math.roundToInt

/**
 * 直接写成 PDF：每页一条 JPEG 流（DCTDecode），不经过打印框架。
 *
 * 这样做的好处是体积完全可控 —— 站点单个文件限 10MB，
 * 需要在生成阶段就知道每一页占多少字节，超了就降质量重来。
 */
object PdfWriter {

    private const val A4_WIDTH = 595.28f
    private const val A4_HEIGHT = 841.89f

    data class Result(val file: File, val bytes: Long, val longEdge: Int, val quality: Int)

    /**
     * 把若干页写成 PDF，超过目标体积就依次降质量、降分辨率。
     * longEdge 是长边像素，质量最低仍超出时才会降分辨率。
     */
    fun write(pages: List<Bitmap>, target: File, maxBytes: Int = SiteConstants.PDF_TARGET_BYTES): Result {
        var quality = 82
        var longEdge = 2200
        var attempt = 0
        while (true) {
            val jpegs = pages.map { encode(it, longEdge, quality) }
            val pdf = buildPdf(jpegs)
            target.parentFile?.mkdirs()
            target.writeBytes(pdf)
            if (pdf.size <= maxBytes || attempt >= 4) {
                return Result(target, target.length(), longEdge, quality)
            }
            // 体积主要来自 JPEG：先降质量，仍超再降分辨率
            if (quality > 55) quality -= 12 else longEdge = (longEdge * 0.8f).roundToInt()
            attempt++
        }
    }

    private fun encode(bitmap: Bitmap, longEdge: Int, quality: Int): ByteArray {
        val scaled = DocDetect.downscale(bitmap, longEdge)
        val out = ByteArrayOutputStream()
        // PDF 要 RGB，先丢掉 alpha 免得某些阅读器渲染成黑底
        val opaque = Bitmap.createBitmap(scaled.width, scaled.height, Bitmap.Config.ARGB_8888).also {
            val canvas = android.graphics.Canvas(it)
            canvas.drawColor(android.graphics.Color.WHITE)
            canvas.drawBitmap(scaled, 0f, 0f, null)
        }
        opaque.compress(Bitmap.CompressFormat.JPEG, quality, out)
        if (opaque !== scaled) opaque.recycle()
        if (scaled !== bitmap && scaled !== opaque) scaled.recycle()
        return out.toByteArray()
    }

    private fun buildPdf(jpegs: List<ByteArray>): ByteArray {
        val objects = ArrayList<ByteArray>()
        // 1 catalog, 2 pages, 3..(2+n) page, 后面 content 与 image
        objects.add("<< /Type /Catalog /Pages 2 0 R >>".toByteArray())
        objects.add(ByteArray(0)) // pages 占位，稍后回填

        val pageIds = ArrayList<Int>()
        val contentIds = ArrayList<Int>()
        val imageIds = ArrayList<Int>()

        var nextId = 3
        repeat(jpegs.size) {
            pageIds.add(nextId++)
            contentIds.add(nextId++)
            imageIds.add(nextId++)
        }

        val kids = pageIds.joinToString(" ") { "$it 0 R" }
        objects[1] = "<< /Type /Pages /Kids [$kids] /Count ${pageIds.size} >>".toByteArray()

        jpegs.forEachIndexed { index, jpeg ->
            val content = "q ${A4_WIDTH} 0 0 ${A4_HEIGHT} 0 0 cm /Im0 Do Q"
            val page = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 $A4_WIDTH $A4_HEIGHT] " +
                "/Resources << /XObject << /Im0 ${imageIds[index]} 0 R >> >> " +
                "/Contents ${contentIds[index]} 0 R >>"
            objects.add(page.toByteArray())
            val stream = content.toByteArray()
            objects.add(
                ("<< /Length ${stream.size} >>\nstream\n").toByteArray() +
                    stream + "\nendstream".toByteArray()
            )
            val imageHeader = "<< /Type /XObject /Subtype /Image /Width ${imageWidth(jpeg)} " +
                "/Height ${imageHeight(jpeg)} /ColorSpace /DeviceRGB /BitsPerComponent 8 " +
                "/Filter /DCTDecode /Length ${jpeg.size} >>\nstream\n"
            objects.add(imageHeader.toByteArray() + jpeg + "\nendstream".toByteArray())
        }

        val out = ByteArrayOutputStream()
        out.write("%PDF-1.4\n".toByteArray())
        val offsets = IntArray(objects.size)
        objects.forEachIndexed { index, bytes ->
            offsets[index] = out.size()
            out.write("${index + 1} 0 obj\n".toByteArray())
            out.write(bytes)
            out.write("\nendobj\n".toByteArray())
        }
        val xrefOffset = out.size()
        out.write("xref\n0 ${objects.size + 1}\n".toByteArray())
        out.write("0000000000 65535 f \n".toByteArray())
        offsets.forEach { out.write("%010d 00000 n \n".format(it).toByteArray()) }
        out.write(
            "trailer\n<< /Size ${objects.size + 1} /Root 1 0 R >>\nstartxref\n$xrefOffset\n%%EOF".toByteArray()
        )
        return out.toByteArray()
    }

    /** JPEG 尺寸直接从 SOF 段读，免得为拿宽高再多存一份 Bitmap。 */
    private fun imageWidth(jpeg: ByteArray): Int = sof(jpeg)?.second ?: 1000
    private fun imageHeight(jpeg: ByteArray): Int = sof(jpeg)?.first ?: 1400

    private fun sof(jpeg: ByteArray): Pair<Int, Int>? {
        var i = 2
        while (i + 9 < jpeg.size) {
            if (jpeg[i].toInt() and 0xFF != 0xFF) { i++; continue }
            val marker = jpeg[i + 1].toInt() and 0xFF
            val length = ((jpeg[i + 2].toInt() and 0xFF) shl 8) or (jpeg[i + 3].toInt() and 0xFF)
            if (marker in 0xC0..0xCF && marker != 0xC4 && marker != 0xC8 && marker != 0xCC) {
                val h = ((jpeg[i + 5].toInt() and 0xFF) shl 8) or (jpeg[i + 6].toInt() and 0xFF)
                val w = ((jpeg[i + 7].toInt() and 0xFF) shl 8) or (jpeg[i + 8].toInt() and 0xFF)
                return h to w
            }
            i += 2 + length
        }
        return null
    }
}
