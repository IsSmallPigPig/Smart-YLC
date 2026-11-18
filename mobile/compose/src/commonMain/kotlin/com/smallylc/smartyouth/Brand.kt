package com.smallylc.smartyouth

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Notes
import androidx.compose.foundation.layout.Box

@Composable
fun BrandMark(size: androidx.compose.ui.unit.Dp = 34.dp) {
    androidx.compose.foundation.Canvas(
        Modifier
            .size(size)
            .clip(RoundedCornerShape(size * 0.26f))
            .background(Color(0xFFFDFDFD)),
    ) {
        val u = this.size.minDimension / 100f
        val cx = 50f * u
        val cy = 52f * u
        val lineBrush = Brush.radialGradient(
            listOf(Color(0xFFFF8B99), Color(0xFFF4737F), Color(0xFFD95C6E)),
            center = androidx.compose.ui.geometry.Offset(cx, cy),
            radius = 34f * u,
        )
        repeat(6) { i ->
            val angle = kotlin.math.PI / 2 + i * kotlin.math.PI / 3
            val dx = kotlin.math.cos(angle).toFloat()
            val dy = -kotlin.math.sin(angle).toFloat()
            drawLine(
                brush = lineBrush,
                start = androidx.compose.ui.geometry.Offset(cx + dx * 9f * u, cy + dy * 9f * u),
                end = androidx.compose.ui.geometry.Offset(cx + dx * 25f * u, cy + dy * 25f * u),
                strokeWidth = 3.4f * u,
                cap = androidx.compose.ui.graphics.StrokeCap.Round,
            )
            drawCircle(
                brush = lineBrush,
                radius = 5.6f * u,
                center = androidx.compose.ui.geometry.Offset(cx + dx * 27f * u, cy + dy * 27f * u),
            )
        }
        drawCircle(
            brush = Brush.radialGradient(
                listOf(Color(0xFFFF97A6), Color(0xFFE52B47)),
                center = androidx.compose.ui.geometry.Offset(cx - 4f * u, cy - 5f * u),
                radius = 24f * u,
            ),
            radius = 13f * u,
            center = androidx.compose.ui.geometry.Offset(cx, cy),
        )
        val sx = 72f * u
        val sy = 27f * u
        val sparkle = androidx.compose.ui.graphics.Path().apply {
            moveTo(sx, sy - 6f * u)
            lineTo(sx + 1.6f * u, sy - 1.6f * u)
            lineTo(sx + 6f * u, sy)
            lineTo(sx + 1.6f * u, sy + 1.6f * u)
            lineTo(sx, sy + 6f * u)
            lineTo(sx - 1.6f * u, sy + 1.6f * u)
            lineTo(sx - 6f * u, sy)
            lineTo(sx - 1.6f * u, sy - 1.6f * u)
            close()
        }
        drawPath(sparkle, Color(0xFFF4737F))
    }
}

