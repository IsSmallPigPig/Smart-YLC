package com.smallylc.smartyouth.ui.screen.theme

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.data.repository.SettingsRepositoryImpl
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.IconButton
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.SmallTitle
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Back
import top.yukonga.miuix.kmp.preference.OverlayDropdownPreference
import top.yukonga.miuix.kmp.preference.SwitchPreference
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic

@Composable
fun ThemeScreen(onBack: () -> Unit) {
    val repo = remember { SettingsRepositoryImpl() }
    var colorMode by remember { mutableIntStateOf(repo.themeMode) }
    var keyColor by remember { mutableIntStateOf(repo.keyColor) }
    var blur by remember { mutableStateOf(repo.enableBlur) }
    var monet by remember { mutableStateOf(repo.miuixMonet) }
    var colorStyle by remember { mutableStateOf(repo.colorStyle) }
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) Color.Transparent else colorScheme.surface

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = "主题",
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
                    OverlayDropdownPreference(
                        title = "深色模式",
                        summary = "跟随系统时按时间自动切换",
                        items = listOf("跟随系统", "浅色", "深色"),
                        selectedIndex = colorMode.coerceIn(0, 2),
                        onSelectedIndexChange = {
                            colorMode = it
                            repo.themeMode = it
                        },
                    )
                    SwitchPreference(
                        title = "动态取色",
                        summary = "用系统壁纸或选定主色生成整套配色",
                        checked = monet,
                        onCheckedChange = {
                            monet = it
                            repo.miuixMonet = it
                        },
                    )
                    OverlayDropdownPreference(
                        title = "取色风格",
                        summary = "影响动态取色的鲜艳程度",
                        items = paletteNames,
                        selectedIndex = paletteNames.indexOf(colorStyle).coerceAtLeast(0),
                        onSelectedIndexChange = {
                            colorStyle = paletteNames[it]
                            repo.colorStyle = colorStyle
                        },
                    )
                    SwitchPreference(
                        title = "界面模糊",
                        summary = "顶栏与底栏用实时模糊，关闭更省电",
                        checked = blur,
                        onCheckedChange = {
                            blur = it
                            repo.enableBlur = it
                        },
                    )
                }
            }

            item { SmallTitle("主题色") }
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        MutedText("选一个主色，按钮、选中态和进度条都跟着变。")
                        androidx.compose.foundation.layout.FlowRow(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(top = 14.dp),
                            horizontalArrangement = Arrangement.spacedBy(14.dp),
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            swatches.forEach { swatch ->
                                val selected = keyColor == swatch.first
                                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                    Box(
                                        modifier = Modifier
                                            .size(38.dp)
                                            .clip(CircleShape)
                                            .background(if (swatch.first == 0) Color(0xFF8A8A8E) else Color(swatch.first))
                                            .then(
                                                if (selected) Modifier.border(3.dp, colorScheme.onBackground, CircleShape)
                                                else Modifier
                                            )
                                            .clickable {
                                                keyColor = swatch.first
                                                repo.keyColor = swatch.first
                                            },
                                    )
                                    Text(
                                        text = swatch.second,
                                        fontSize = 11.sp,
                                        color = colorScheme.onSurfaceVariantSummary,
                                        modifier = Modifier.padding(top = 4.dp),
                                    )
                                }
                            }
                        }
                        Spacer(Modifier.height(6.dp))
                    }
                }
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }
}

private val swatchNames = listOf("蓝", "青", "绿", "草绿", "紫", "玫红", "珊瑚", "灰蓝")
private val swatches = com.smallylc.smartyouth.ui.theme.keyColorOptions
    .mapIndexed { index, color -> color to swatchNames[index] } + listOf(0 to "壁纸")

private val paletteNames = listOf("TonalSpot", "Vibrant", "Neutral", "Expressive", "Rainbow", "Monochrome", "Fidelity")
