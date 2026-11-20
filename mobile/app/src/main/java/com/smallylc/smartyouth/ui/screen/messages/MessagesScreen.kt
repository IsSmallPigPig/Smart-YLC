package com.smallylc.smartyouth.ui.screen.messages

import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.site.SiteMessage
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.EmptyHint
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.StatusPill
import com.smallylc.smartyouth.ui.component.ToneBlue
import com.smallylc.smartyouth.ui.component.ToneGreen
import com.smallylc.smartyouth.ui.component.ToneRed
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.CardDefaults
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TextButton
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.overlay.OverlayDialog
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic

private enum class MessageFilter(val label: String, val key: String?) {
    All("全部", null),
    Report("报到", "report"),
    Transfer("转接", "transfer"),
    Other("其他", "other"),
}

@Composable
fun MessagesScreen(
    loggedIn: Boolean,
    messages: List<SiteMessage>,
    syncing: Boolean,
    error: String?,
    bottomPadding: Dp,
    onLogin: () -> Unit,
) {
    var filter by rememberSaveable { mutableStateOf(MessageFilter.All) }
    var detail by remember { mutableStateOf<SiteMessage?>(null) }
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) Color.Transparent else colorScheme.surface
    val unread = messages.count { it.unread }

    val filtered = remember(messages, filter) {
        if (filter.key == null) messages else messages.filter { it.kind == filter.key }
    }

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = "站内信",
                    subtitle = if (unread > 0) "$unread 条未读" else "",
                    color = barColor,
                    scrollBehavior = scrollBehavior,
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
                .padding(horizontal = 26.dp),
            contentPadding = innerPadding,
            overscrollEffect = null,
        ) {
            item { Spacer(Modifier.height(8.dp)) }
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    MessageFilter.entries.forEach { entry ->
                        val count = if (entry.key == null) messages.size else messages.count { it.kind == entry.key }
                        Card(
                            onClick = { filter = entry },
                            cornerRadius = 18.dp,
                            colors = CardDefaults.defaultColors(
                                color = if (filter == entry) colorScheme.primary else colorScheme.secondaryContainer,
                            ),
                            insideMargin = androidx.compose.foundation.layout.PaddingValues(
                                horizontal = 14.dp,
                                vertical = 7.dp,
                            ),
                        ) {
                            Text(
                                text = "${entry.label} $count",
                                fontSize = 13.sp,
                                color = if (filter == entry) colorScheme.onPrimary else colorScheme.onSurface,
                            )
                        }
                    }
                }
            }

            when {
                messages.isEmpty() && !loggedIn -> item {
                    CardGap()
                    Card(Modifier.fillMaxWidth(), onClick = onLogin) {
                        Column(Modifier.padding(20.dp)) {
                            Text("登录后同步站内信", fontSize = 15.sp, color = colorScheme.onSurface)
                            MutedText("报到与转接申请的审核提醒都在这里。", Modifier.padding(top = 4.dp))
                        }
                    }
                }

                filtered.isEmpty() -> item {
                    EmptyHint(
                        title = when {
                            syncing -> "正在同步站内信…"
                            error != null -> "站内信加载失败"
                            else -> "没有这类消息"
                        },
                        detail = error,
                    )
                }

                else -> items(filtered, key = { it.id + it.title }) { message ->
                    MessageRow(message) { detail = message }
                    CardGap(8)
                }
            }

            item { Spacer(Modifier.height(bottomPadding + 8.dp)) }
        }
    }

    detail?.let { message ->
        OverlayDialog(
            show = true,
            title = message.title.ifBlank { "站内信" },
            summary = message.timeStr,
            onDismissRequest = { detail = null },
            content = {
                Column {
                    Text(
                        text = message.content.ifBlank { "（这条消息没有正文）" },
                        fontSize = 14.sp,
                        color = colorScheme.onSurface,
                        modifier = Modifier.padding(bottom = 12.dp),
                    )
                    if (message.mobile.isNotBlank()) {
                        MutedText("团员手机号 ${message.mobile}", Modifier.padding(bottom = 12.dp))
                    }
                    TextButton(
                        text = "我知道了",
                        onClick = { detail = null },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            },
        )
    }
}

@Composable
private fun MessageRow(message: SiteMessage, onClick: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth(), onClick = onClick) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = message.title.ifBlank { "（无标题）" },
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Medium,
                    color = colorScheme.onSurface,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                when (message.kind) {
                    "report" -> StatusPill(message.kindLabel, ToneBlue)
                    "transfer" -> StatusPill(message.kindLabel, ToneGreen)
                    else -> StatusPill(message.kindLabel, ToneRed)
                }
            }
            Text(
                text = message.content,
                fontSize = 13.sp,
                color = colorScheme.onSurfaceVariantSummary,
                maxLines = 2,
                modifier = Modifier.padding(top = 4.dp),
            )
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (message.unread) {
                    Text("未读", fontSize = 11.sp, color = ToneRed)
                    Spacer(Modifier.width(8.dp))
                }
                MutedText(message.timeStr)
            }
        }
    }
}
