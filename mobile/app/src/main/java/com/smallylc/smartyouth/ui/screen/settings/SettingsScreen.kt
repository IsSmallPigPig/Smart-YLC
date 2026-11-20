package com.smallylc.smartyouth.ui.screen.settings

import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.BuildConfig
import com.smallylc.smartyouth.data.ScanPrefs
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.SmallTitle
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Info
import top.yukonga.miuix.kmp.icon.extended.Theme
import top.yukonga.miuix.kmp.preference.ArrowPreference
import top.yukonga.miuix.kmp.preference.OverlayDropdownPreference
import top.yukonga.miuix.kmp.preference.SwitchPreference
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic

@Composable
fun SettingsScreen(
    userName: String,
    loggedIn: Boolean,
    siteDown: Boolean,
    syncing: Boolean,
    memberCount: Int,
    accountCount: Int,
    bottomPadding: Dp,
    onAccounts: () -> Unit,
    onLogout: () -> Unit,
    onOpenTheme: () -> Unit,
    onOpenAbout: () -> Unit,
    onSync: () -> Unit,
) {
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) Color.Transparent else colorScheme.surface
    var autoUpload by remember { mutableStateOf(ScanPrefs.autoUpload) }
    var filterIndex by remember {
        mutableStateOf(
            when (ScanPrefs.defaultFilter) {
                "BW" -> 2
                "ORIGINAL" -> 0
                else -> 1
            }
        )
    }

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(title = "我的", color = barColor, scrollBehavior = scrollBehavior)
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
                .padding(horizontal = 16.dp),
            contentPadding = innerPadding,
            overscrollEffect = null,
        ) {
            item {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 8.dp),
                ) {
                    Column(Modifier.padding(16.dp)) {
                        Text(
                            text = when {
                                siteDown -> "站点维护中"
                                loggedIn -> userName
                                else -> "未登录"
                            },
                            fontSize = 18.sp,
                            color = colorScheme.onSurface,
                        )
                        MutedText(
                            text = when {
                                siteDown -> "账号已保存，维护结束后自动恢复"
                                !loggedIn -> "登录支部账号后才能同步名单与上传"
                                syncing -> "正在同步…"
                                else -> "本机已缓存 $memberCount 名团员"
                            },
                            modifier = Modifier.padding(top = 4.dp),
                        )
                    }
                    ArrowPreference(
                        title = "账号管理",
                        summary = if (accountCount > 0) "共 $accountCount 个账号，点开切换" else "添加或从云端同步账号",
                        onClick = onAccounts,
                    )
                    if (loggedIn) {
                        ArrowPreference(
                            title = "刷新名单与消息",
                            summary = "重新从站点拉取",
                            onClick = onSync,
                        )
                    }
                }
            }

            item { SmallTitle("扫描") }
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    OverlayDropdownPreference(
                        title = "默认画面处理",
                        summary = "拍摄后每页仍可单独改",
                        items = listOf("原色", "增强", "黑白"),
                        selectedIndex = filterIndex,
                        onSelectedIndexChange = { index ->
                            filterIndex = index
                            ScanPrefs.defaultFilter = when (index) {
                                0 -> "ORIGINAL"
                                1 -> "ENHANCE"
                                else -> "BW"
                            }
                        },
                    )
                    SwitchPreference(
                        title = "保存后立即上传",
                        summary = "生成 PDF 后自动登录并提交审核",
                        checked = autoUpload,
                        onCheckedChange = {
                            autoUpload = it
                            ScanPrefs.autoUpload = it
                        },
                    )
                }
            }

            item { SmallTitle("外观") }
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    ArrowPreference(
                        title = "主题与配色",
                        summary = "深色模式、取色与模糊效果",
                        startAction = {
                            Icon(
                                imageVector = MiuixIcons.Theme,
                                contentDescription = null,
                                tint = colorScheme.onBackground,
                                modifier = Modifier.padding(end = 6.dp),
                            )
                        },
                        onClick = onOpenTheme,
                    )
                    ArrowPreference(
                        title = "关于智慧团委",
                        summary = "版本 ${BuildConfig.VERSION_NAME}",
                        startAction = {
                            Icon(
                                imageVector = MiuixIcons.Info,
                                contentDescription = null,
                                tint = colorScheme.onBackground,
                                modifier = Modifier.padding(end = 6.dp),
                            )
                        },
                        onClick = onOpenAbout,
                    )
                }
            }

            if (loggedIn) {
                item { CardGap() }
                item {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        ArrowPreference(
                            title = "退出登录",
                            summary = "仅清除本机会话，不影响站点密码",
                            onClick = onLogout,
                        )
                    }
                }
            }

            item { Spacer(Modifier.height(bottomPadding + 16.dp)) }
        }
    }
}
