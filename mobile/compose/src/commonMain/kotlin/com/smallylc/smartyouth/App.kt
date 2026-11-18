package com.smallylc.smartyouth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import top.yukonga.miuix.kmp.basic.NavigationBar
import top.yukonga.miuix.kmp.basic.NavigationBarItem
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Community
import top.yukonga.miuix.kmp.icon.extended.File
import top.yukonga.miuix.kmp.icon.extended.Home
import top.yukonga.miuix.kmp.icon.extended.Messages
import top.yukonga.miuix.kmp.icon.extended.Notes
import top.yukonga.miuix.kmp.theme.MiuixTheme

private data class Tab(val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector, val hint: String)

private val TABS = listOf(
    Tab("首页", MiuixIcons.Home, "完整度、异常与未读"),
    Tab("档案", MiuixIcons.File, "扫描、收件箱与提交"),
    Tab("支部", MiuixIcons.Community, "支部进度与成员"),
    Tab("消息", MiuixIcons.Messages, "站内信与已读"),
    Tab("我的", MiuixIcons.Notes, "账号与名单"),
)

@Composable
fun App() {
    var tab by remember { mutableStateOf(1) }
    Scaffold(
        bottomBar = {
            NavigationBar {
                TABS.forEachIndexed { index, item ->
                    NavigationBarItem(
                        selected = tab == index,
                        onClick = { tab = index },
                        icon = item.icon,
                        label = item.label,
                    )
                }
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                BrandMark(64.dp)
                Text(TABS[tab].label, style = MiuixTheme.textStyles.main.copy(fontSize = 22.sp, fontWeight = FontWeight.Bold))
                Text(TABS[tab].hint, style = MiuixTheme.textStyles.main.copy(fontSize = 13.sp))
            }
        }
    }
}
