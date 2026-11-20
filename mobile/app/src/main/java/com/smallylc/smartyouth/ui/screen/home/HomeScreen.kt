package com.smallylc.smartyouth.ui.screen.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.add
import androidx.compose.foundation.layout.displayCutout
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.BuildConfig
import com.smallylc.smartyouth.site.Overview
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.StatCell
import com.smallylc.smartyouth.ui.component.ToneAmber
import com.smallylc.smartyouth.ui.component.ToneBlue
import com.smallylc.smartyouth.ui.component.ToneGreen
import com.smallylc.smartyouth.ui.component.ToneRed
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import top.yukonga.miuix.kmp.basic.Button
import top.yukonga.miuix.kmp.basic.ButtonDefaults
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.IconButton
import top.yukonga.miuix.kmp.basic.LinearProgressIndicator
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Refresh
import top.yukonga.miuix.kmp.icon.extended.Scan
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * 首页：先回答「支部现在什么状态」，再给能立刻动手的入口。
 * 在线、维护中、未登录分开写——站点维护时显示成未登录会误导。
 */
@Composable
fun HomeScreen(
    loggedIn: Boolean,
    userName: String,
    overview: Overview,
    syncing: Boolean,
    lastSync: Long,
    error: String?,
    siteDown: Boolean,
    bottomPadding: Dp,
    onLogin: () -> Unit,
    onScan: () -> Unit,
    onSync: () -> Unit,
    onOpenRoster: (filter: String?) -> Unit,
    onOpenMessages: () -> Unit,
) {
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) Color.Transparent else colorScheme.surface

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = "智慧团委",
                    color = barColor,
                    scrollBehavior = scrollBehavior,
                    actions = {
                        IconButton(onClick = onSync, enabled = loggedIn && !syncing) {
                            Icon(
                                imageVector = MiuixIcons.Refresh,
                                contentDescription = "同步名单",
                                tint = colorScheme.onBackground,
                            )
                        }
                    },
                )
            }
        },
        popupHost = { },
        contentWindowInsets = WindowInsets.systemBars.add(WindowInsets.displayCutout)
            .only(WindowInsetsSides.Horizontal),
    ) { innerPadding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxHeight()
                .scrollEndHaptic()
                .overScrollVertical()
                .nestedScroll(scrollBehavior.nestedScrollConnection)
                .statusBarsPadding()
                .padding(horizontal = 16.dp),
            contentPadding = innerPadding,
            overscrollEffect = null,
        ) {
            item { Spacer(Modifier.height(8.dp)) }

            item { StatusHero(loggedIn = loggedIn, userName = userName, siteDown = siteDown) }

            item {
                StatusChip(
                    loggedIn = loggedIn,
                    syncing = syncing,
                    siteDown = siteDown,
                    error = error,
                    lastSync = lastSync,
                    overview = overview,
                    onLogin = onLogin,
                )
            }

            if (loggedIn && overview.total > 0) {
                item {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        MetricCard("${overview.total}", "团员", Modifier.weight(1f))
                        MetricCard("${overview.abnormal}", "待处理", Modifier.weight(1f))
                    }
                }
            }

            item { CardGap(12) }
            item { FactsCard(loggedIn, userName, lastSync, overview) }

            if (loggedIn && overview.total > 0) {
                item { CardGap() }
                item { ProgressCard(overview) }
                item { CardGap() }
                item { ProblemCard(overview, onOpenRoster) }
                item { CardGap() }
                item { MessageCard(overview.unreadMessages, onOpenMessages) }
            }

            item { CardGap() }
            item { ScanCard(onScan) }
            item { Spacer(Modifier.height(bottomPadding + 12.dp)) }
        }
    }
}

/**
 * 主状态卡底色。主题的 primaryContainer 由取色算法生成，深色模式下会被算成亮蓝，
 * 和品牌观感不一致；这里固定品牌深蓝，两套主题都不再随算法漂移。
 */
private val HeroBackground = Color(0xFF25406B)

/** 主状态卡：一块底色讲清当前能不能用。 */
@Composable
private fun StatusHero(loggedIn: Boolean, userName: String, siteDown: Boolean) {
    val label = when {
        siteDown -> "维护中"
        loggedIn -> "在线"
        else -> "未登录"
    }
    val hint = when {
        siteDown -> "站点正在维护，恢复后自动同步"
        loggedIn -> userName
        else -> "登录支部账号后开始"
    }
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(22.dp))
            .background(HeroBackground),
    ) {
        HeroMark(
            tint = Color.White,
            modifier = Modifier.matchParentSize(),
        )
        Column(Modifier.padding(20.dp)) {
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .background(Color.White.copy(alpha = 0.14f))
                    .padding(horizontal = 10.dp, vertical = 4.dp),
            ) {
                Text(text = "站点", fontSize = 12.sp, color = Color.White)
            }
            Text(
                text = label,
                fontSize = 32.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White,
                modifier = Modifier.padding(top = 14.dp),
            )
            Text(
                text = hint,
                fontSize = 14.sp,
                color = Color.White.copy(alpha = 0.78f),
                modifier = Modifier.padding(top = 6.dp),
            )
            Text(
                text = "版本 ${BuildConfig.VERSION_NAME}",
                fontSize = 13.sp,
                color = Color.White.copy(alpha = 0.62f),
                modifier = Modifier.padding(top = 18.dp),
            )
        }
    }
}

/** 品牌标志：六向辐射网络，从右下角探入卡片，扁平叠色做出光感。 */
@Composable
private fun HeroMark(tint: Color, modifier: Modifier = Modifier) {
    Canvas(modifier) {
        val cx = size.width * 0.97f
        val cy = size.height * 0.93f
        val reach = size.height * 0.66f
        val unit = reach / 26f
        val hub = Offset(cx, cy)

        drawCircle(
            brush = Brush.radialGradient(
                colors = listOf(tint.copy(alpha = 0.24f), Color.Transparent),
                center = hub,
                radius = reach * 1.55f,
            ),
            radius = reach * 1.55f,
            center = hub,
        )

        repeat(6) { i ->
            val angle = (-90 + 60 * i) * PI / 180.0
            val end = Offset(
                cx + (cos(angle) * reach).toFloat(),
                cy + (sin(angle) * reach).toFloat(),
            )
            drawLine(
                color = tint.copy(alpha = 0.24f),
                start = hub,
                end = end,
                strokeWidth = 1.6.dp.toPx(),
                cap = StrokeCap.Round,
            )
            drawCircle(tint.copy(alpha = 0.10f), radius = 6.5f * unit, center = end)
            drawCircle(tint.copy(alpha = 0.42f), radius = 4f * unit, center = end)
            drawCircle(tint.copy(alpha = 0.62f), radius = 1.4f * unit, center = end)
        }

        drawCircle(tint.copy(alpha = 0.10f), radius = 14f * unit, center = hub)
        drawCircle(Color(0xFFF4737F).copy(alpha = 0.62f), radius = 8.5f * unit, center = hub)
        drawCircle(
            Color.White.copy(alpha = 0.34f),
            radius = 4f * unit,
            center = Offset(cx - 2f * unit, cy - 2f * unit),
        )
    }
}

/** 胶囊状态条：同步进度、维护提示、失败原因都放这里。 */
@Composable
private fun StatusChip(
    loggedIn: Boolean,
    syncing: Boolean,
    siteDown: Boolean,
    error: String?,
    lastSync: Long,
    overview: Overview,
    onLogin: () -> Unit,
) {
    val text: String
    val tone: Color
    when {
        siteDown -> {
            text = "站点正在维护，数据暂时取不到"
            tone = ToneAmber
        }
        syncing -> {
            text = "正在同步名单…"
            tone = colorScheme.primary
        }
        error != null -> {
            text = "同步失败：$error"
            tone = ToneRed
        }
        !loggedIn -> {
            text = "登录后可同步名单与消息"
            tone = colorScheme.primary
        }
        lastSync > 0 -> {
            text = "名单已同步  ${relativeTime(lastSync)}"
            tone = ToneGreen
        }
        else -> {
            text = "名单尚未同步"
            tone = ToneAmber
        }
    }
    Card(
        modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
        cornerRadius = 22.dp,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = text,
                fontSize = 13.sp,
                color = tone,
                modifier = Modifier.weight(1f),
                maxLines = 2,
            )
            if (!loggedIn && !siteDown) {
                Button(
                    onClick = onLogin,
                    colors = ButtonDefaults.buttonColorsPrimary(),
                ) {
                    Text("登录")
                }
            } else if (loggedIn) {
                Text(
                    text = "${overview.uploaded}/${overview.total}",
                    fontSize = 13.sp,
                    color = colorScheme.onSurfaceVariantSummary,
                )
            }
        }
    }
}

@Composable
private fun MetricCard(value: String, label: String, modifier: Modifier = Modifier) {
    Card(modifier = modifier, cornerRadius = 18.dp) {
        Column(Modifier.padding(16.dp)) {
            Text(
                text = value,
                fontSize = 30.sp,
                fontWeight = FontWeight.SemiBold,
                color = colorScheme.onSurface,
            )
            Text(
                text = label,
                fontSize = 13.sp,
                color = colorScheme.onSurfaceVariantSummary,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
    }
}

/** 设备信息式列表：左边项目名，右边值。 */
@Composable
private fun FactsCard(loggedIn: Boolean, userName: String, lastSync: Long, overview: Overview) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
            FactRow("账号", userName.ifBlank { "未登录" })
            FactRow("团员", if (overview.total > 0) "${overview.total} 人" else "—")
            FactRow("已上传", if (overview.total > 0) "${overview.uploaded} 份" else "—")
            FactRow("最近同步", if (lastSync > 0) relativeTime(lastSync) else "—")
            FactRow("版本", BuildConfig.VERSION_NAME)
        }
    }
}

@Composable
private fun FactRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = label,
            fontSize = 14.sp,
            color = colorScheme.onSurfaceVariantSummary,
            modifier = Modifier.weight(1f),
        )
        Text(text = value, fontSize = 14.sp, color = colorScheme.onSurface)
    }
}

@Composable
private fun ProgressCard(overview: Overview) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.Bottom) {
                Text(
                    text = "${(overview.ratio * 100).toInt()}",
                    fontSize = 34.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = colorScheme.onSurface,
                )
                Text(
                    text = "%",
                    fontSize = 16.sp,
                    color = colorScheme.onSurfaceVariantSummary,
                    modifier = Modifier.padding(bottom = 5.dp, start = 2.dp),
                )
                Spacer(Modifier.weight(1f))
                Text(
                    text = "已上传 ${overview.uploaded} / ${overview.total}",
                    fontSize = 13.sp,
                    color = colorScheme.onSurfaceVariantSummary,
                )
            }
            LinearProgressIndicator(
                progress = overview.ratio,
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
            )
            MutedText("电子志愿书上传进度", Modifier.padding(top = 8.dp))
        }
    }
}

@Composable
private fun ProblemCard(overview: Overview, onOpenRoster: (String?) -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(vertical = 8.dp)) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "需要处理",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Medium,
                    color = colorScheme.onSurface,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = "共 ${overview.abnormal} 人",
                    fontSize = 13.sp,
                    color = if (overview.abnormal > 0) ToneAmber else ToneGreen,
                )
            }
            Row(Modifier.fillMaxWidth()) {
                StatCell("${overview.identity}", "信息错误", Modifier.weight(1f), if (overview.identity > 0) ToneRed else null) {
                    onOpenRoster("identity")
                }
                StatCell("${overview.report}", "没有报到", Modifier.weight(1f), if (overview.report > 0) ToneAmber else null) {
                    onOpenRoster("report")
                }
                StatCell("${overview.archive}", "没有档案", Modifier.weight(1f), if (overview.archive > 0) ToneBlue else null) {
                    onOpenRoster("archive")
                }
                StatCell("${overview.fees}", "欠缴团费", Modifier.weight(1f), if (overview.fees > 0) ToneAmber else null) {
                    onOpenRoster("fees")
                }
            }
        }
    }
}

@Composable
private fun MessageCard(unread: Int, onOpenMessages: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth(), onClick = onOpenMessages) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("站内信", fontSize = 16.sp, fontWeight = FontWeight.Medium, color = colorScheme.onSurface)
                MutedText(
                    if (unread > 0) "$unread 条未读" else "没有未读消息",
                    Modifier.padding(top = 3.dp),
                )
            }
            if (unread > 0) {
                Text(
                    text = "$unread",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Medium,
                    color = colorScheme.primary,
                )
            }
        }
    }
}

@Composable
private fun ScanCard(onScan: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = MiuixIcons.Scan,
                    contentDescription = null,
                    tint = colorScheme.primary,
                )
                Text(
                    text = "扫描入团志愿书",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Medium,
                    color = colorScheme.onSurface,
                    modifier = Modifier.padding(start = 8.dp),
                )
            }
            MutedText(
                "自动裁边摆正，按团员姓名命名后直接上传。",
                Modifier.padding(top = 6.dp),
            )
        }
        Button(
            onClick = onScan,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp)
                .height(44.dp),
            colors = ButtonDefaults.buttonColorsPrimary(),
        ) {
            Text("开始扫描")
        }
    }
}

private fun relativeTime(ts: Long): String {
    if (ts <= 0) return "—"
    val minutes = (System.currentTimeMillis() - ts) / 60000
    return when {
        minutes < 1 -> "刚刚"
        minutes < 60 -> "$minutes 分钟前"
        minutes < 1440 -> "${minutes / 60} 小时前"
        else -> "${minutes / 1440} 天前"
    }
}
