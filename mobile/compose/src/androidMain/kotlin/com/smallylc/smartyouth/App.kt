package com.smallylc.smartyouth

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import top.yukonga.miuix.kmp.basic.FloatingNavigationBar
import top.yukonga.miuix.kmp.basic.FloatingNavigationBarItem
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.blur.layerBackdrop
import top.yukonga.miuix.kmp.blur.rememberLayerBackdrop
import top.yukonga.miuix.kmp.blur.textureBlur
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Community
import top.yukonga.miuix.kmp.icon.extended.File
import top.yukonga.miuix.kmp.icon.extended.Home
import top.yukonga.miuix.kmp.icon.extended.Messages
import top.yukonga.miuix.kmp.icon.extended.Notes
import top.yukonga.miuix.kmp.material.isRuntimeShaderSupported
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
    val surface = MiuixTheme.colorScheme.surface
    val glass = isRuntimeShaderSupported()
    val backdrop = rememberLayerBackdrop {
        drawRect(surface)
        drawContent()
    }

    Box(Modifier.fillMaxSize()) {
        // 内容层：被底栏采样
        Box(Modifier.fillMaxSize().background(surface).layerBackdrop(backdrop)) {
            if (tab == 1) {
                FilesScreen()
            } else {
                Column(
                    Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(10.dp, Alignment.CenterVertically),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    BrandMark(64.dp)
                    Text(TABS[tab].label, style = MiuixTheme.textStyles.main.copy(fontSize = 22.sp, fontWeight = FontWeight.Bold))
                    Text(TABS[tab].hint, style = MiuixTheme.textStyles.main.copy(fontSize = 13.sp))
                }
            }
        }

        // 底部玻璃胶囊：采样内容层做模糊；低版本退回不透明
        Box(
            Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 16.dp).navigationBarsPadding()
                .height(64.dp)
                .then(
                    if (glass) {
                        Modifier.textureBlur(
                            backdrop = backdrop,
                            shape = RoundedCornerShape(30.dp),
                            blurRadius = 5f,
                            noiseCoefficient = 0.12f,
                        )
                    } else {
                        Modifier.background(surface, RoundedCornerShape(30.dp))
                    },
                ),
            contentAlignment = Alignment.Center,
        ) {
            FloatingNavigationBar(
                modifier = Modifier.fillMaxWidth(),
                color = Color.Transparent,
            ) {
                TABS.forEachIndexed { index, item ->
                    FloatingNavigationBarItem(
                        selected = tab == index,
                        onClick = { tab = index },
                        icon = item.icon,
                        label = item.label,
                    )
                }
            }
        }
    }
}
