package com.smallylc.smartyouth.ui.screen.members

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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
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
import com.smallylc.smartyouth.site.Member
import com.smallylc.smartyouth.site.trimAmount
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.EmptyHint
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.StatusPill
import com.smallylc.smartyouth.ui.component.ToneAmber
import com.smallylc.smartyouth.ui.component.ToneBlue
import com.smallylc.smartyouth.ui.component.ToneGreen
import com.smallylc.smartyouth.ui.component.ToneRed
import com.smallylc.smartyouth.ui.component.ValueRow
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.CardDefaults
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TextButton
import top.yukonga.miuix.kmp.basic.TextField
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.basic.ButtonDefaults
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.overlay.OverlayDialog
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic

/** 名单筛选：一个筛选条件对应一类「需要处理的人」。 */
enum class RosterFilter(val label: String, val key: String?) {
    All("全部", null),
    Pending("未上传", "pending"),
    Identity("信息错误", "identity"),
    Report("没有报到", "report"),
    Archive("没有档案", "archive"),
    Fees("欠费", "fees"),
}

@Composable
fun MembersScreen(
    loggedIn: Boolean,
    members: List<Member>,
    initialFilter: String?,
    bottomPadding: Dp,
    onLogin: () -> Unit,
    onScanFor: (Member) -> Unit,
) {
    var query by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable {
        mutableStateOf(RosterFilter.entries.firstOrNull { it.key == initialFilter } ?: RosterFilter.All)
    }
    var detail by remember { mutableStateOf<Member?>(null) }
    val listState = rememberLazyListState()
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) Color.Transparent else colorScheme.surface

    val filtered = remember(members, query, filter) {
        members.filter { m ->
            val textOk = query.isBlank() ||
                m.name.contains(query.trim(), ignoreCase = true) ||
                m.mobile.contains(query.trim())
            val filterOk = when (filter) {
                RosterFilter.All -> true
                RosterFilter.Pending -> !m.uploaded
                RosterFilter.Identity -> !m.identityOk
                RosterFilter.Report -> !m.reportOk && !m.archiveMissing
                RosterFilter.Archive -> m.archiveMissing
                RosterFilter.Fees -> !m.feesOk
            }
            textOk && filterOk
        }
    }

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = if (members.isEmpty()) "团员名单" else "团员 ${members.size}",
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
            state = listState,
            modifier = Modifier
                .fillMaxHeight()
                .scrollEndHaptic()
                .overScrollVertical()
                .nestedScroll(scrollBehavior.nestedScrollConnection)
                .padding(horizontal = 12.dp),
            contentPadding = innerPadding,
            overscrollEffect = null,
        ) {
            item {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 8.dp),
                ) {
                    TextField(
                        value = query,
                        onValueChange = { query = it },
                        modifier = Modifier.fillMaxWidth(),
                        label = "搜索姓名或手机号",
                    )
                }
            }
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState())
                        .padding(vertical = 10.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    RosterFilter.entries.forEach { entry ->
                        FilterChip(
                            text = entry.label,
                            selected = filter == entry,
                            onClick = { filter = entry },
                        )
                    }
                }
            }

            if (members.isEmpty() && !loggedIn) {
                item {
                    Card(Modifier.fillMaxWidth(), onClick = onLogin) {
                        Column(Modifier.padding(20.dp)) {
                            Text("登录后同步团员名单", fontSize = 15.sp, color = colorScheme.onSurface)
                            MutedText("名单会缓存在本机，断网也能按姓名补齐文件名。", Modifier.padding(top = 4.dp))
                        }
                    }
                }
            } else if (filtered.isEmpty()) {
                item {
                    EmptyHint(
                        title = when {
                            members.isEmpty() && !loggedIn -> "登录后同步团员名单"
                            members.isEmpty() -> "名单还没同步"
                            else -> "没有符合条件的人"
                        },
                        detail = if (members.isEmpty()) "回到首页点右上角刷新，或检查账号是否已登录。" else null,
                    )
                }
            } else {
                items(filtered, key = { it.mid + it.name }) { member ->
                    MemberRow(member) { detail = member }
                    CardGap(8)
                }
            }

            item { Spacer(Modifier.height(bottomPadding + 8.dp)) }
        }
    }

    detail?.let { member ->
        MemberDetailDialog(
            member = member,
            onDismiss = { detail = null },
            onScan = {
                detail = null
                onScanFor(member)
            },
        )
    }
}

@Composable
private fun FilterChip(text: String, selected: Boolean, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        cornerRadius = 18.dp,
        colors = CardDefaults.defaultColors(
            color = if (selected) colorScheme.primary else colorScheme.secondaryContainer,
        ),
        insideMargin = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 7.dp),
    ) {
        Text(
            text = text,
            fontSize = 13.sp,
            color = if (selected) colorScheme.onPrimary else colorScheme.onSurface,
        )
    }
}

@Composable
private fun MemberRow(member: Member, onClick: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth(), onClick = onClick) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = member.name.ifBlank { "（无名）" },
                        fontSize = 15.sp,
                        fontWeight = FontWeight.Medium,
                        color = colorScheme.onSurface,
                    )
                    Spacer(Modifier.width(8.dp))
                    MutedText(member.mobile)
                }
                MutedText(
                    text = member.displayOrg.ifBlank { "—" },
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                if (member.uploaded) StatusPill("已上传", ToneGreen)
                else StatusPill("未上传", ToneAmber)
                val problem = member.problems().firstOrNull()
                if (problem != null) {
                    Text(
                        text = problem,
                        fontSize = 11.sp,
                        color = ToneRed,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun MemberDetailDialog(member: Member, onDismiss: () -> Unit, onScan: () -> Unit) {
    OverlayDialog(
        show = true,
        title = member.name.ifBlank { "团员" },
        summary = member.mobile,
        onDismissRequest = onDismiss,
        content = {
            Column {
                ValueRow("支部", member.displayOrg.ifBlank { "—" })
                ValueRow("报到资料", member.reportStatus.ifBlank { "—" },
                    valueColor = if (member.reportOk) ToneGreen else ToneAmber)
                ValueRow("实名认证", member.realNameStatus.ifBlank { "—" },
                    valueColor = if (member.identityOk) ToneGreen else ToneRed)
                ValueRow("电子志愿书", member.ebookStatus.ifBlank { "—" },
                    valueColor = if (member.uploaded) ToneGreen else ToneBlue)
                ValueRow(
                    "团费",
                    if (member.feesOk) "无欠缴" else buildString {
                        if (member.unpaidTotal > 0) append("${trimAmount(member.unpaidTotal)} 元 ")
                        if (member.unpaidMonths > 0) append("${member.unpaidMonths} 个月")
                    }.trim(),
                    valueColor = if (member.feesOk) ToneGreen else ToneAmber,
                )
                if (member.joinDate.isNotBlank()) ValueRow("入团时间", member.joinDate)

                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    TextButton(
                        text = "关闭详情",
                        onClick = onDismiss,
                        modifier = Modifier.weight(1f),
                    )
                    Spacer(Modifier.width(16.dp))
                    TextButton(
                        text = "扫描档案",
                        onClick = onScan,
                        modifier = Modifier.weight(1.6f),
                        colors = ButtonDefaults.textButtonColorsPrimary(),
                    )
                }
            }
        },
    )
}
