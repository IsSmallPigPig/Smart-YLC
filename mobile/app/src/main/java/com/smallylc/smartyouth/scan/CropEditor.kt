package com.smallylc.smartyouth.scan

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.ui.component.MutedText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.CardDefaults
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * 四角调整。照片保持原始比例铺在黑底里，四个把手直接拖，
 * 外侧压暗到 45% —— 不用文字说明也能看出哪块会被裁掉。
 */
@Composable
fun CropEditor(
    page: ScanPage,
    onChangeQuad: (DocDetect.Quad) -> Unit,
    onChangeFilter: (DocDetect.Filter) -> Unit,
    modifier: Modifier = Modifier,
) {
    var bitmap by remember(page.id, page.rotation) { mutableStateOf<Bitmap?>(null) }
    var canvasSize by remember { mutableStateOf(Size.Zero) }
    var dragIndex by remember { mutableStateOf(-1) }
    var preview by remember(page.id, page.rotation) { mutableStateOf<Bitmap?>(null) }

    LaunchedEffect(page.id, page.rotation) {
        bitmap = withContext(Dispatchers.IO) {
            runCatching {
                val file = page.file
                val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
                android.graphics.BitmapFactory.decodeFile(file.absolutePath, bounds)
                var sample = 1
                while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 2000) sample *= 2
                android.graphics.BitmapFactory.decodeFile(
                    file.absolutePath,
                    android.graphics.BitmapFactory.Options().apply {
                        inSampleSize = sample
                        inPreferredConfig = Bitmap.Config.ARGB_8888
                    },
                )
            }.getOrNull()
        }
    }

    // 裁切或滤镜变了才重算小预览，拖拽过程中不动它
    LaunchedEffect(page.quad, page.filter, page.id, page.rotation) {
        // 不主动回收旧图：它可能还在渲染队列里，回收会直接闪退
        preview = ScanSession.render(page, 420)
    }

    Column(modifier) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .background(Color.Black)
                .onSizeChanged { canvasSize = Size(it.width.toFloat(), it.height.toFloat()) },
            contentAlignment = Alignment.Center,
        ) {
            val bmp = bitmap
            if (bmp != null && canvasSize.width > 0f) {
                val fit = fitRect(bmp.width, bmp.height, canvasSize)
                Canvas(
                    modifier = Modifier
                        .fillMaxSize()
                        .pointerInput(page.id, bmp, canvasSize) {
                            detectDragGestures(
                                onDragStart = { offset ->
                                    dragIndex = nearestHandle(page.quad, fit, offset)
                                },
                                onDragEnd = { dragIndex = -1 },
                                onDragCancel = { dragIndex = -1 },
                            ) { change, drag ->
                                change.consume()
                                val index = dragIndex
                                if (index >= 0) {
                                    val point = page.quad.points[index]
                                    val nx = ((point.first * bmp.width * fit.scale + fit.left) + drag.x) / (bmp.width * fit.scale)
                                    val ny = ((point.second * bmp.height * fit.scale + fit.top) + drag.y) / (bmp.height * fit.scale)
                                    val clamped = DocDetect.clamp(nx, ny)
                                    val updated = page.quad.points.toMutableList().also { it[index] = clamped }
                                    onChangeQuad(DocDetect.Quad(updated))
                                }
                            }
                        },
                ) {
                    val image = bmp.asImageBitmap()
                    drawImage(
                        image = image,
                        dstOffset = IntOffset(fit.left.toInt(), fit.top.toInt()),
                        dstSize = IntSize((bmp.width * fit.scale).toInt(), (bmp.height * fit.scale).toInt()),
                    )
                    drawQuadOverlay(page.quad, bmp.width, bmp.height, fit)
                }
            }
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            FilterChip("原色", page.filter == DocDetect.Filter.ORIGINAL) { onChangeFilter(DocDetect.Filter.ORIGINAL) }
            FilterChip("增强", page.filter == DocDetect.Filter.ENHANCE) { onChangeFilter(DocDetect.Filter.ENHANCE) }
            FilterChip("黑白", page.filter == DocDetect.Filter.BW) { onChangeFilter(DocDetect.Filter.BW) }
            Spacer(Modifier.width(4.dp))
            MutedText("拖四个角对齐纸张边缘", Modifier.padding(start = 4.dp))
        }

        preview?.let { p ->
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp)
                    .padding(bottom = 12.dp),
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    androidx.compose.foundation.Image(
                        bitmap = p.asImageBitmap(),
                        contentDescription = null,
                        modifier = Modifier
                            .height(56.dp)
                            .width(42.dp),
                    )
                    Column(Modifier.padding(start = 12.dp)) {
                        Text(
                            text = "导出效果",
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Medium,
                            color = colorScheme.onSurface,
                        )
                        MutedText(
                            when (page.filter) {
                                DocDetect.Filter.ORIGINAL -> "保留原始色彩"
                                DocDetect.Filter.ENHANCE -> "压平阴影，字迹更清楚"
                                DocDetect.Filter.BW -> "纯黑白，体积最小"
                            },
                            Modifier.padding(top = 2.dp),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun FilterChip(text: String, selected: Boolean, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        cornerRadius = 16.dp,
        colors = CardDefaults.defaultColors(
            color = if (selected) colorScheme.primary else colorScheme.secondaryContainer,
        ),
        insideMargin = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 6.dp),
    ) {
        Text(
            text = text,
            fontSize = 13.sp,
            color = if (selected) colorScheme.onPrimary else colorScheme.onSurface,
        )
    }
}

private data class FitRect(val left: Float, val top: Float, val scale: Float)

private fun fitRect(bmpW: Int, bmpH: Int, canvas: Size): FitRect {
    val scale = min(canvas.width / bmpW, canvas.height / bmpH)
    val drawW = bmpW * scale
    val drawH = bmpH * scale
    return FitRect((canvas.width - drawW) / 2f, (canvas.height - drawH) / 2f, scale)
}

private fun nearestHandle(quad: DocDetect.Quad, fit: FitRect, offset: Offset): Int {
    // 画布坐标 → 归一化后用画布尺寸近似即可（fit 已含缩放，这里只比相对距离）
    var best = -1
    var bestDistance = Float.MAX_VALUE
    quad.points.forEachIndexed { index, (nx, ny) ->
        val x = nx * fit.scale + fit.left
        val y = ny * fit.scale + fit.top
        val d = (offset.x - x) * (offset.x - x) + (offset.y - y) * (offset.y - y)
        if (d < bestDistance) {
            bestDistance = d
            best = index
        }
    }
    return best
}

private fun DrawScope.drawQuadOverlay(quad: DocDetect.Quad, bmpW: Int, bmpH: Int, fit: FitRect) {
    val points = quad.points.map { (nx, ny) ->
        Offset(nx * bmpW * fit.scale + fit.left, ny * bmpH * fit.scale + fit.top)
    }
    val outer = Path().apply {
        addRect(androidx.compose.ui.geometry.Rect(0f, 0f, size.width, size.height))
        val inner = Path().apply {
            moveTo(points[0].x, points[0].y)
            lineTo(points[1].x, points[1].y)
            lineTo(points[2].x, points[2].y)
            lineTo(points[3].x, points[3].y)
            close()
        }
        addPath(inner)
        fillType = PathFillType.EvenOdd
    }
    drawPath(outer, Color.Black.copy(alpha = 0.45f))

    val outline = Path().apply {
        moveTo(points[0].x, points[0].y)
        lineTo(points[1].x, points[1].y)
        lineTo(points[2].x, points[2].y)
        lineTo(points[3].x, points[3].y)
        close()
    }
    drawPath(outline, Color.White, style = androidx.compose.ui.graphics.drawscope.Stroke(width = 2f * density))

    points.forEach { p ->
        drawCircle(Color.White, radius = 9f * density, center = p)
        drawCircle(Color(0xFF3B82F6), radius = 6f * density, center = p)
    }
}
