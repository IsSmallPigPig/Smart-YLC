package com.smallylc.smartyouth.scan

import android.graphics.Bitmap
import org.opencv.android.Utils
import org.opencv.core.Core
import org.opencv.core.CvType
import org.opencv.core.Mat
import org.opencv.core.MatOfPoint
import org.opencv.core.MatOfPoint2f
import org.opencv.core.Point
import org.opencv.core.RotatedRect
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

    private const val DETECT_MAX_EDGE = 1080f

    fun detect(bitmap: Bitmap): Quad? {
        val scale = (DETECT_MAX_EDGE / max(bitmap.width, bitmap.height)).coerceAtMost(1f)
        val small = if (scale < 1f) {
            Bitmap.createScaledBitmap(
                bitmap,
                (bitmap.width * scale).toInt(),
                (bitmap.height * scale).toInt(),
                true,
            )
        } else {
            bitmap
        }

        val src = Mat()
        val gray = Mat()
        val lab = Mat()
        val std = Mat()
        val masks = ArrayList<Mat>()
        return try {
            Utils.bitmapToMat(small, src)
            Imgproc.cvtColor(src, gray, Imgproc.COLOR_RGBA2GRAY)
            val bgr = Mat()
            Imgproc.cvtColor(src, bgr, Imgproc.COLOR_RGBA2BGR)
            Imgproc.cvtColor(bgr, lab, Imgproc.COLOR_BGR2Lab)
            bgr.release()

            val channels = ArrayList<Mat>()
            Core.split(lab, channels)

            // 亮度与色度各做一次 Otsu，正反两种极性都留下：
            // 白纸配深桌、灰纸配木墙、纸比背景暗，都能覆盖到
            for (channel in channels) {
                val blurred = Mat()
                Imgproc.GaussianBlur(channel, blurred, Size(5.0, 5.0), 0.0)
                val binary = Mat()
                Imgproc.threshold(blurred, binary, 0.0, 255.0, Imgproc.THRESH_BINARY + Imgproc.THRESH_OTSU)
                val inverted = Mat()
                Core.bitwise_not(binary, inverted)
                masks.add(binary)
                masks.add(inverted)
                blurred.release()
            }
            channels.forEach { it.release() }

            // 纸是画面里「亮且平」的一块：木纹、床单条纹、布料褶皱都带局部起伏
            val grayF = Mat()
            gray.convertTo(grayF, CvType.CV_32F)
            val mean = Mat()
            Imgproc.boxFilter(grayF, mean, CvType.CV_32F, Size(25.0, 25.0))
            val square = Mat()
            Core.multiply(grayF, grayF, square)
            Imgproc.boxFilter(square, square, CvType.CV_32F, Size(25.0, 25.0))
            val meanSquare = Mat()
            Core.multiply(mean, mean, meanSquare)
            Core.subtract(square, meanSquare, std)
            Core.sqrt(std, std)
            std.convertTo(std, CvType.CV_8U)
            val bright = Mat()
            Imgproc.threshold(gray, bright, 105.0, 255.0, Imgproc.THRESH_BINARY)
            val flat = Mat()
            Imgproc.threshold(std, flat, 14.0, 255.0, Imgproc.THRESH_BINARY_INV)
            Core.bitwise_and(bright, flat, flat)
            masks.add(flat)
            grayF.release(); mean.release(); square.release(); meanSquare.release()
            bright.release()

            val imageArea = small.width.toDouble() * small.height
            val kernelOpen = Imgproc.getStructuringElement(Imgproc.MORPH_RECT, Size(7.0, 7.0))
            val kernelClose = Imgproc.getStructuringElement(Imgproc.MORPH_RECT, Size(17.0, 17.0))

            var bestQuad: Quad? = null
            var bestScore = Double.NEGATIVE_INFINITY

            for (mask in masks) {
                val cleaned = mask.clone()
                Imgproc.morphologyEx(cleaned, cleaned, Imgproc.MORPH_OPEN, kernelOpen)
                Imgproc.morphologyEx(cleaned, cleaned, Imgproc.MORPH_CLOSE, kernelClose)
                val contours = ArrayList<MatOfPoint>()
                Imgproc.findContours(cleaned, contours, Mat(), Imgproc.RETR_EXTERNAL, Imgproc.CHAIN_APPROX_SIMPLE)
                cleaned.release()

                val largest = contours.maxByOrNull { Imgproc.contourArea(it) } ?: continue
                val area = Imgproc.contourArea(largest)
                val ratio = area / imageArea
                val rect = Imgproc.minAreaRect(MatOfPoint2f(*largest.toArray()))
                val rectArea = rect.size.width * rect.size.height
                val fill = if (rectArea <= 0.0) 0.0 else area / rectArea
                val longSide = max(rect.size.width, rect.size.height)
                val shortSide = min(rect.size.width, rect.size.height)
                val aspect = if (shortSide <= 0.0) 0.0 else longSide / shortSide

                // 框住整幅画面没有意义；过小的色块也不是纸
                if (ratio <= 0.08 || ratio >= 0.72) continue
                if (fill <= 0.68 || aspect <= 1.0 || aspect >= 3.2) continue

                val score = fill - kotlin.math.abs(aspect - 1.6) * 0.06
                if (score <= bestScore) continue

                val quad = quadFrom(largest, rect, small.width, small.height)
                if (quad != null) {
                    bestScore = score
                    bestQuad = quad
                }
            }
            bestQuad
        } catch (t: Throwable) {
            android.util.Log.w("DocDetect", "detect failed", t)
            null
        } finally {
            src.release(); gray.release(); lab.release(); std.release()
            masks.forEach { it.release() }
            if (small !== bitmap) small.recycle()
        }
    }

    /** 能近似成四点就取四点，否则退到最小外接矩形。 */
    private fun quadFrom(contour: MatOfPoint, rect: RotatedRect, width: Int, height: Int): Quad? {
        val curve = MatOfPoint2f(*contour.toArray())
        val perimeter = Imgproc.arcLength(curve, true)
        val approx = MatOfPoint2f()
        Imgproc.approxPolyDP(curve, approx, 0.02 * perimeter, true)
        val pts = approx.toArray()
        val ordered = if (pts.size == 4 && Imgproc.isContourConvex(MatOfPoint(*pts))) {
            orderCorners(pts)
        } else {
            val corners = arrayOfNulls<Point>(4)
            rect.points(corners)
            orderCorners(corners.filterNotNull().toTypedArray())
        }
        curve.release(); approx.release()
        if (ordered.size != 4) return null
        return toQuad(ordered, width, height)
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
