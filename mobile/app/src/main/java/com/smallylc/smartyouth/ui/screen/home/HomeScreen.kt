package com.smallylc.smartyouth.ui.screen.home

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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.site.Overview
import com.smallylc.smartyouth.site.trimAmount
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.DotLabel
import com.smallylc.smartyouth.ui.component.EmptyHint
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.StatCell
import com.smallylc.smartyouth.ui.component.ToneAmber
import com.smallylc.smartyouth.ui.component.ToneBlue
import com.smallylc.smartyouth.ui.component.ToneGreen
import com.smallylc.smartyouth.ui.component.ToneRed
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.theme.isInDarkTheme
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

/**
 * 首页：先把「这个支部整体怎么样」讲清楚，再给一个能立刻动手的扫描入口。
 * 没登录时不展示空统计，直接给登录卡片 —— 空数字没有信息量。
 */
@Composable
fun HomeScreen(
    loggedIn: Boolean,
    userName: String,
    overview: Overview,
    syncing: Boolean,
    lastSync: Long,
    error: String?,
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
                .padding(horizontal = 12.dp),
            contentPadding = innerPadding,
            overscrollEffect = null,
        ) {
            item { Spacer(Modifier.height(8.dp)) }

            if (!loggedIn) {
                item {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(20.dp)) {
                            Text(
                                text = "还没登录支部账号",
                                fontSize = 17.sp,
                                fontWeight = FontWeight.Medium,
                                color = colorScheme.onSurface,
                            )
                            MutedText(
                                text = "登录后可以同步团员名单、上传志愿书、查看站内信。",
                                modifier = Modifier.padding(top = 6.dp),
                            )
                            Button(
                                onClick = onLogin,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(top = 16.dp),
                                colors = ButtonDefaults.buttonColorsPrimary(),
                            ) {
                                Text("登录账号")
                            }
                        }
                    }
                }
            } else {
                item { AccountCard(userName, syncing, lastSync, error) }
            }

            if (loggedIn || overview.total > 0) {
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

@Composable
private fun AccountCard(userName: String, syncing: Boolean, lastSync: Long, error: String?) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    text = userName,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Medium,
                    color = colorScheme.onSurface,
                )
                val status = when {
                    syncing -> "正在同步名单…"
                    error != null -> "同步失败：$error"
                    lastSync > 0 -> "名单已同步 · ${relativeTime(lastSync)}"
                    else -> "名单尚未同步"
                }
                Text(
                    text = status,
                    fontSize = 13.sp,
                    color = if (error != null) ToneRed else colorScheme.onSurfaceVariantSummary,
                    modifier = Modifier.padding(top = 3.dp),
                )
            }
        }
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
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 10.dp),
            )
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                MutedText("电子志愿书上传进度")
                MutedText("异常 ${overview.abnormal} 人", Modifier.then(Modifier))
            }
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
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("站内信", fontSize = 16.sp, fontWeight = FontWeight.Medium, color = colorScheme.onSurface)
                MutedText(
                    if (unread > 0) "$unread 条未读，进来看看有没有新的报到申请" else "没有未读消息",
                    Modifier.padding(top = 3.dp),
                )
            }
            if (unread > 0) {
                Text(
                    text = if (unread > 99) "99+" else "$unread",
                    fontSize = 13.sp,
                    color = ToneRed,
                    fontWeight = FontWeight.Medium,
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
                    modifier = Modifier.size(22.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text("扫描入团志愿书", fontSize = 16.sp, fontWeight = FontWeight.Medium, color = colorScheme.onSurface)
            }
            MutedText(
                "拍纸质志愿书，自动摆正裁边，按团员姓名命名后直接上传。",
                Modifier.padding(top = 6.dp),
            )
            Button(
                onClick = onScan,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 14.dp),
                colors = ButtonDefaults.buttonColorsPrimary(),
            ) {
                Text("开始扫描")
            }
        }
    }
}

internal fun relativeTime(ts: Long): String {
    val diff = System.currentTimeMillis() - ts
    val minute = 60_000L
    val hour = 60 * minute
    val day = 24 * hour
    return when {
        diff < minute -> "刚刚"
        diff < hour -> "${diff / minute} 分钟前"
        diff < day -> "${diff / hour} 小时前"
        diff < 2 * day -> "昨天"
        else -> "${diff / day} 天前"
    }
}
