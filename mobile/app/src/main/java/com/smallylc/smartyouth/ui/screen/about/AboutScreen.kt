package com.smallylc.smartyouth.ui.screen.about

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.BuildConfig
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.ValueRow
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.IconButton
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Back
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic

@Composable
fun AboutScreen(onBack: () -> Unit) {
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) Color.Transparent else colorScheme.surface

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = "关于",
                    color = barColor,
                    scrollBehavior = scrollBehavior,
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(MiuixIcons.Back, contentDescription = "返回", tint = colorScheme.onBackground)
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
            item {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 8.dp),
                ) {
                    Column(Modifier.padding(20.dp)) {
                        androidx.compose.foundation.Image(
                            painter = androidx.compose.ui.res.painterResource(com.smallylc.smartyouth.R.drawable.app_logo),
                            contentDescription = null,
                            modifier = Modifier
                                .size(64.dp)
                                .padding(bottom = 10.dp),
                        )
                        Text(
                            text = "智慧团委",
                            fontSize = 22.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = colorScheme.onSurface,
                        )
                        MutedText(
                            "手机端档案工具：扫描纸质志愿书、按团员姓名命名，直接上传到站点。",
                            Modifier.padding(top = 6.dp),
                        )
                    }
                }
            }

            item {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 12.dp),
                ) {
                    ValueRow("版本", BuildConfig.VERSION_NAME)
                    ValueRow("构建号", BuildConfig.VERSION_CODE.toString())
                    ValueRow("站点", "tuan.12355.net")
                    ValueRow("界面", "Miuix Compose")
                }
            }

            item {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 12.dp),
                ) {
                    Column(Modifier.padding(16.dp)) {
                        Row {
                            Text("账号与数据", fontSize = 15.sp, fontWeight = FontWeight.Medium, color = colorScheme.onSurface)
                        }
                        MutedText(
                            "账号密码只保存在本机私有目录，会话 Cookie 按账号分开保存。" +
                                "扫描件存放在本机「扫描件」目录，删除应用会一并清除。",
                            Modifier.padding(top = 6.dp),
                        )
                    }
                }
            }

            item {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 12.dp),
                ) {
                    Column(Modifier.padding(16.dp)) {
                        Text("开源许可", fontSize = 15.sp, fontWeight = FontWeight.Medium, color = colorScheme.onSurface)
                        MutedText(
                            "界面基于 KernelSU Style UI Kit（GPL-3.0）与 Miuix 组件库。",
                            Modifier.padding(top = 6.dp),
                        )
                    }
                }
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }
}
