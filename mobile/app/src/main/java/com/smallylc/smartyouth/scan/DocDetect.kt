package com.smallylc.smartyouth.scan

import android.graphics.Bitmap
import org.opencv.android.Utils
import org.opencv.core.Core
import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.core.MatOfPoint
import org.opencv.core.MatOfPoint2f
import org.opencv.core.Point
import org.opencv.core.Size
import org.opencv.imgproc.Imgproc
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * 纸张检测与校正。做法是扫描类应用的通用套路：
 * 缩图 → 灰度 → 模糊 → Canny → 找最大四边形轮廓 → 透视变换。
 *
 * 检出的四角用 0..1 的归一化坐标保存，与显示尺寸无关，旋转屏幕或换预览尺寸都不会丢。
 */
object DocDetect {

    enum class Filter { ORIGINAL, ENHANCE, BW }

    /** 归一化四角，顺序固定：左上、右上、右下、左下。 */
    data class Quad(val points: List<Pair<Float, Float>>) {
        fun toPoints(width: Int, height: Int): List<Point> =
            points.map { (x, y) -> Point(x * width.toDouble(), y * height.toDouble()) }

        companion object {
            val FULL = Quad(listOf(0f to 0f, 1f to 0f, 1f to 1f, 0f to 1f))
        }
    }

    private const val DETECT_MAX_EDGE = 900f

    fun detect(bitmap: Bitmap): Quad? {
        val scale = (DETECT_MAX_EDGE / max(bitmap.width, bitmap.height)).coerceAtMost(1f)
        val small = if (scale < 1f) {
            Bitmap.createScaledBitmap(bitmap, (bitmap.width * scale).toInt(), (bitmap.height * scale).toInt(), true)
        } else bitmap

        val src = Mat()
        val gray = Mat()
        val edges = Mat()
        return try {
            Utils.bitmapToMat(small, src)
            Imgproc.cvtColor(src, gray, Imgproc.COLOR_RGBA2GRAY)
            Imgproc.GaussianBlur(gray, gray, Size(5.0, 5.0), 0.0)
            Imgproc.Canny(gray, edges, 60.0, 180.0)
            // 闭运算把断裂的边缘接起来，纸张四边在手机拍的照片里常被手指/阴影切断
            val kernel = Imgproc.getStructuringElement(Imgproc.MORPH_RECT, Size(5.0, 5.0))
            Imgproc.morphologyEx(edges, edges, Imgproc.MORPH_CLOSE, kernel)

            val contours = ArrayList<MatOfPoint>()
            Imgproc.findContours(edges, contours, Mat(), Imgproc.RETR_LIST, Imgproc.CHAIN_APPROX_SIMPLE)

            var best: Quad? = null
            var bestArea = 0.0
            val imageArea = small.width.toDouble() * small.height

            for (contour in contours) {
                val curve = MatOfPoint2f(*contour.toArray())
                val approx = MatOfPoint2f()
                val perimeter = Imgproc.arcLength(curve, true)
                Imgproc.approxPolyDP(curve, approx, 0.02 * perimeter, true)
                val pts = approx.toArray()
                if (pts.size == 4 && Imgproc.isContourConvex(MatOfPoint(*pts))) {
                    val area = Imgproc.contourArea(MatOfPoint(*pts))
                    if (area > imageArea * 0.15 && area > bestArea) {
                        bestArea = area
                        best = toQuad(orderCorners(pts), small.width, small.height)
                    }
                }
            }
            best
        } catch (t: Throwable) {
            null
        } finally {
            src.release(); gray.release(); edges.release()
            if (small !== bitmap) small.recycle()
        }
    }

    /** 归一化用的起点：左上。 */
    private fun toQuad(ordered: List<Point>, width: Int, height: Int): Quad =
        Quad(ordered.map { (it.x / width).toFloat() to (it.y / height).toFloat() })

    private fun orderCorners(points: Array<Point>): List<Point> {
        // 左上角和右下角分别是 x+y 最小/最大；右上与左下看 x-y
        val sortedBySum = points.sortedBy { it.x + it.y }
        val tl = sortedBySum.first()
        val br = sortedBySum.last()
        val rest = points.filter { it !== tl && it !== br }
        val (tr, bl) = rest.sortedBy { it.x - it.y }.let { ordered ->
            ordered.last() to ordered.first()
        }
        return listOf(tl, tr, br, bl)
    }

    /** 透视校正 + 可选的增强，输出给 PDF 用。 */
    fun rectify(source: Bitmap, quad: Quad, filter: Filter): Bitmap {
        val src = Mat()
        val dst = Mat()
        return try {
            Utils.bitmapToMat(source, src)
            val w = source.width.toDouble()
            val h = source.height.toDouble()
            val pts = quad.toPoints(source.width, source.height)

            val widthTop = hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y)
            val widthBottom = hypot(pts[2].x - pts[3].x, pts[2].y - pts[3].y)
            val heightLeft = hypot(pts[3].x - pts[0].x, pts[3].y - pts[0].y)
            val heightRight = hypot(pts[2].x - pts[1].x, pts[2].y - pts[1].y)
            var outW = max(widthTop, widthBottom).toInt().coerceAtLeast(1)
            var outH = max(heightLeft, heightRight).toInt().coerceAtLeast(1)
            if (outW > w || outH > h) {
                val k = min(w / outW, h / outH)
                outW = (outW * k).toInt().coerceAtLeast(1)
                outH = (outH * k).toInt().coerceAtLeast(1)
            }

            val srcMat = MatOfPoint2f(*pts.toTypedArray())
            val dstMat = MatOfPoint2f(
                Point(0.0, 0.0),
                Point(outW - 1.0, 0.0),
                Point(outW - 1.0, outH - 1.0),
                Point(0.0, outH - 1.0),
            )
            val transform = Imgproc.getPerspectiveTransform(srcMat, dstMat)
            Imgproc.warpPerspective(src, dst, transform, Size(outW.toDouble(), outH.toDouble()))

            if (filter != Filter.ORIGINAL) {
                val gray = Mat()
                Imgproc.cvtColor(dst, gray, Imgproc.COLOR_RGBA2GRAY)
                if (filter == Filter.BW) {
                    Imgproc.adaptiveThreshold(
                        gray, gray, 255.0, Imgproc.ADAPTIVE_THRESH_GAUSSIAN_C,
                        Imgproc.THRESH_BINARY, 25, 12.0,
                    )
                    Imgproc.cvtColor(gray, dst, Imgproc.COLOR_GRAY2RGBA)
                } else {
                    // 增强：只提亮 Lab 的亮度通道，色度原样保留，扫出来仍是彩色
                    val rgb = Mat()
                    Imgproc.cvtColor(dst, rgb, Imgproc.COLOR_RGBA2RGB)
                    val lab = Mat()
                    Imgproc.cvtColor(rgb, lab, Imgproc.COLOR_RGB2Lab)
                    val channels = ArrayList<Mat>()
                    Core.split(lab, channels)
                    val clahe = Imgproc.createCLAHE(2.0, Size(8.0, 8.0))
                    clahe.apply(channels[0], channels[0])
                    Core.merge(channels, lab)
                    Imgproc.cvtColor(lab, rgb, Imgproc.COLOR_Lab2RGB)
                    Imgproc.cvtColor(rgb, dst, Imgproc.COLOR_RGB2RGBA)
                    channels.forEach { it.release() }
                    lab.release()
                    rgb.release()
                }
                gray.release()
            }

            // 统一到 8UC4，Utils.bitmapToMat 的逆过程才不吃格式亏
            if (dst.type() != CvType.CV_8UC4) {
                val converted = Mat()
                dst.convertTo(converted, CvType.CV_8UC4)
                dst.release()
                Bitmap.createBitmap(converted.cols(), converted.rows(), Bitmap.Config.ARGB_8888).also {
                    Utils.matToBitmap(converted, it)
                    converted.release()
                }
            } else {
                Bitmap.createBitmap(dst.cols(), dst.rows(), Bitmap.Config.ARGB_8888).also {
                    Utils.matToBitmap(dst, it)
                }
            }
        } catch (t: Throwable) {
            source
        } finally {
            src.release(); dst.release()
        }
    }

    /** 缩放到长边不超过 maxEdge，PDF 里 2000px 已经够清晰。 */
    fun downscale(bitmap: Bitmap, maxEdge: Int): Bitmap {
        val longest = max(bitmap.width, bitmap.height)
        if (longest <= maxEdge) return bitmap
        val k = maxEdge.toFloat() / longest
        return Bitmap.createScaledBitmap(
            bitmap,
            (bitmap.width * k).toInt().coerceAtLeast(1),
            (bitmap.height * k).toInt().coerceAtLeast(1),
            true,
        )
    }

    /** 未检出纸张时的兜底：整张图当纸，边距按经验留 1.5%。 */
    fun inset(): Quad = Quad(listOf(0.015f to 0.015f, 0.985f to 0.015f, 0.985f to 0.985f, 0.015f to 0.985f))

    /** 顺时针 90°，同时把四角跟着转，用户旋转后裁切框仍贴在纸上。 */
    fun rotate(bitmap: Bitmap, quad: Quad): Pair<Bitmap, Quad> {
        val matrix = android.graphics.Matrix().apply { postRotate(90f) }
        val rotated = Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
        val rotatedQuad = Quad(quad.points.map { (x, y) -> (1f - y) to x })
        return rotated to rotatedQuad
    }

    /** 编辑界面用的绘制辅助：把归一化四角映射到画布并夹在 0..1。 */
    fun clamp(x: Float, y: Float): Pair<Float, Float> =
        (min(1f, max(0f, x))) to (min(1f, max(0f, y)))
}
