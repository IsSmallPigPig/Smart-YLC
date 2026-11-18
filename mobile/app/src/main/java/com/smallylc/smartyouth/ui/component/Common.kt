package com.smallylc.smartyouth.ui.component

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import com.smallylc.smartyouth.ui.theme.isInDarkTheme
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.CardDefaults
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.theme.MiuixTheme
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme

/**
 * 业务状态色统一在这里映射：
 * - 错误/异常走 Miuix 的 error，深色与动态取色下自动协调；
 * - 信息走 primary，与主色一致；
 * - Miuix 没有成功/警告 token，用明暗自适应的固定色保证对比度。
 */
val ToneRed: Color
    @Composable get() = colorScheme.error
val ToneAmber: Color
    @Composable get() = if (isInDarkTheme()) Color(0xFFFFB74D) else Color(0xFFB26A00)
val ToneGreen: Color
    @Composable get() = if (isInDarkTheme()) Color(0xFF81C784) else Color(0xFF2E7D32)
val ToneBlue: Color
    @Composable get() = colorScheme.primary

/** 数字格：首页统计与异常分布共用，保证两处字号、间距一致。 */
@Composable
fun StatCell(
    value: String,
    label: String,
    modifier: Modifier = Modifier,
    tone: Color? = null,
    onClick: (() -> Unit)? = null,
) {
    Column(
        modifier = modifier
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(vertical = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = value,
            fontSize = 22.sp,
            fontWeight = FontWeight.SemiBold,
            color = tone ?: colorScheme.onSurface,
        )
        Text(
            text = label,
            fontSize = 12.sp,
            color = colorScheme.onSurfaceVariantSummary,
            modifier = Modifier.padding(top = 2.dp),
        )
    }
}

/** 带色点的状态行：异常分类与上传状态都用它，颜色即语义。 */
@Composable
fun DotLabel(
    text: String,
    color: Color,
    modifier: Modifier = Modifier,
) {
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        Canvas(Modifier.size(6.dp)) { drawCircle(color) }
        Spacer(Modifier.width(6.dp))
        Text(text = text, fontSize = 13.sp, color = colorScheme.onSurfaceVariantSummary)
    }
}

@Composable
fun EmptyHint(
    title: String,
    detail: String? = null,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 36.dp, horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = title,
            fontSize = 15.sp,
            color = colorScheme.onSurfaceVariantSummary,
            textAlign = TextAlign.Center,
        )
        if (detail != null) {
            Text(
                text = detail,
                fontSize = 13.sp,
                color = colorScheme.onSurfaceVariantActions,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
    }
}

/** 状态药丸：列表里区分「已上传 / 待上传 / 异常」。 */
@Composable
fun StatusPill(text: String, color: Color, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier,
        cornerRadius = 7.dp,
        colors = CardDefaults.defaultColors(color = color.copy(alpha = 0.12f)),
        insideMargin = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Text(text = text, fontSize = 12.sp, color = color)
    }
}

@Composable
fun ValueRow(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color? = null,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text = label, fontSize = 14.sp, color = colorScheme.onSurface, modifier = Modifier.weight(1f))
        Text(
            text = value,
            fontSize = 14.sp,
            color = valueColor ?: colorScheme.onSurfaceVariantSummary,
        )
    }
}

@Composable
fun MutedText(text: String, modifier: Modifier = Modifier, color: Color? = null) {
    Text(
        text = text,
        fontSize = 13.sp,
        color = color ?: colorScheme.onSurfaceVariantSummary,
        modifier = modifier,
    )
}

@Composable
fun TitleText(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        fontSize = MiuixTheme.textStyles.headline1.fontSize,
        fontWeight = FontWeight.Medium,
        color = colorScheme.onSurface,
        modifier = modifier,
    )
}

@Composable
fun CardGap(height: Int = 12) {
    Spacer(Modifier.height(height.dp))
}
