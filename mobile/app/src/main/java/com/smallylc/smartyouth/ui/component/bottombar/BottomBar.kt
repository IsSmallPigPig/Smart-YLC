package com.smallylc.smartyouth.ui.component.bottombar

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.pager.PagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.job
import kotlinx.coroutines.launch
import top.yukonga.miuix.kmp.basic.Badge
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Contacts
import top.yukonga.miuix.kmp.icon.extended.Home
import top.yukonga.miuix.kmp.icon.extended.Messages
import top.yukonga.miuix.kmp.icon.extended.Scan
import top.yukonga.miuix.kmp.icon.extended.Settings
import top.yukonga.miuix.kmp.theme.MiuixTheme
import top.yukonga.miuix.kmp.utils.springAnimateToPage
import kotlin.math.roundToInt

class MainPagerState(
    val pagerState: PagerState,
    private val coroutineScope: CoroutineScope,
    private val animatePageChanges: Boolean,
) {
    var selectedPage by mutableIntStateOf(pagerState.currentPage)
        private set

    var isNavigating by mutableStateOf(false)
        private set

    private var navJob: Job? = null

    fun animateToPage(targetIndex: Int) {
        if (targetIndex == selectedPage) return
        navJob?.cancel()
        selectedPage = targetIndex
        isNavigating = true
        navJob = coroutineScope.launch {
            val myJob = coroutineContext.job
            try {
                if (animatePageChanges) pagerState.springAnimateToPage(targetIndex)
                else pagerState.scrollToPage(targetIndex)
            } finally {
                if (navJob == myJob) {
                    isNavigating = false
                    if (pagerState.currentPage != targetIndex) selectedPage = pagerState.currentPage
                }
            }
        }
    }

    fun syncPage() {
        if (!isNavigating && selectedPage != pagerState.currentPage) selectedPage = pagerState.currentPage
    }
}

@Composable
fun rememberMainPagerState(
    pagerState: PagerState,
    coroutineScope: CoroutineScope = rememberCoroutineScope(),
    animatePageChanges: Boolean = true,
): MainPagerState = remember(pagerState, coroutineScope, animatePageChanges) {
    MainPagerState(pagerState, coroutineScope, animatePageChanges)
}

/** 平板或横屏用侧栏：宽度 >=600dp 且偏横，或 >=840dp。 */
@Composable
fun useNavigationRail(): Boolean {
    val size = LocalWindowInfo.current.containerSize
    val density = LocalResources.current.displayMetrics.density
    val widthDp = size.width / density
    val heightDp = size.height / density
    return widthDp >= 840f || (widthDp >= 600f && heightDp / widthDp < 1.2f)
}

enum class MainDestination(val label: String, val icon: ImageVector) {
    Home("首页", MiuixIcons.Home),
    Roster("名单", MiuixIcons.Contacts),
    Scan("扫描", MiuixIcons.Scan),
    Messages("消息", MiuixIcons.Messages),
    Settings("我的", MiuixIcons.Settings),
}

// 悬浮胶囊几何：与 Classy 的 dock 一致，留出左右呼吸和手势条间距。
private val BarHeight = 64.dp
private val DockMargin = 26.dp
private val PillWidth = 64.dp

private val PillHeight = 32.dp
private val ThumbHeight = 54.dp
private val ThumbTop = 9.dp
private val IconSize = 22.dp
/** 单个 tab 的固定宽度：整组居中，图标之间不被整条宽度拉开。 */
private val LabelSize = 11.sp

/** 单个 tab 的宽度：图标 22dp、两个汉字约 22dp，两侧各留 4dp。 */
private val PillTop = 6.dp
/** 选中块高度：盖住图标与文字，比整条矮一圈。 */
private val ItemHeight = 58.dp
/** 选中块圆角：背景半径 36dp 减去上下各 7dp 内缩，与背景同心。 */
private val ItemCorner = 29.dp

/**
 * 悬浮胶囊底栏（对齐 KernelSU）：
 * 选中项是一枚实心圆，图标反白、文字取主色；未选中只有浅色图标与灰字。
 * 不做滑动色块，切换时颜色直接落位。
 */
@Composable
fun BottomBar(
    unreadCount: Int,
    mainState: MainPagerState,
    modifier: Modifier = Modifier,
) {
    val colors = MiuixTheme.colorScheme
    val density = LocalDensity.current
    val items = MainDestination.entries

    val thumbX = remember { Animatable(0f) }
    var itemWidthPx by remember { mutableFloatStateOf(0f) }
    val pillWidthPx = with(density) { PillWidth.toPx() }
    var barX by remember { mutableFloatStateOf(0f) }
    val centers = remember { mutableStateListOf(*Array(items.size) { 0f }) }
    val springSpec = spring<Float>(dampingRatio = 0.8f, stiffness = 320f)

    LaunchedEffect(centers.toList(), mainState.selectedPage, itemWidthPx) {
        val index = mainState.selectedPage.coerceIn(0, items.lastIndex)
        if (itemWidthPx <= 0f || centers[index] <= 0f) return@LaunchedEffect
        thumbX.animateTo(centers[index] - barX - pillWidthPx / 2f, springSpec)
    }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .background(colors.surfaceContainer)
            .navigationBarsPadding(),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(BarHeight)
                .onGloballyPositioned { barX = it.positionInRoot().x },
        ) {
            // 选中指示：图标后面一枚滑动的胶囊
            if (itemWidthPx > 0f) {
                Box(
                    modifier = Modifier
                        .padding(top = PillTop)
                        .offset { IntOffset(thumbX.value.roundToInt(), 0) }
                        .width(PillWidth)
                        .height(PillHeight)
                        .clip(RoundedCornerShape(percent = 50))
                        .background(colors.primary.copy(alpha = 0.16f)),
                )
            }
            Row(modifier = Modifier.fillMaxSize()) {
                items.forEachIndexed { index, destination ->
                    val selected = mainState.selectedPage == index
                    val fg by animateColorAsState(
                        targetValue = if (selected) colors.primary else colors.onSurfaceVariantActions,
                        animationSpec = tween(200),
                        label = "tabFg",
                    )
                    val interaction = remember { MutableInteractionSource() }
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight()
                            .clickable(
                                interactionSource = interaction,
                                indication = null,
                            ) { mainState.animateToPage(index) }
                            .onGloballyPositioned {
                                centers[index] = it.positionInRoot().x + it.size.width / 2f
                                if (index == 0) itemWidthPx = it.size.width.toFloat()
                            },
                        contentAlignment = Alignment.Center,
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Box(contentAlignment = Alignment.Center) {
                                Icon(
                                    imageVector = destination.icon,
                                    contentDescription = destination.label,
                                    tint = fg,
                                    modifier = Modifier.size(IconSize),
                                )
                                if (destination == MainDestination.Messages && unreadCount > 0) {
                                    Box(
                                        modifier = Modifier
                                            .align(Alignment.TopEnd)
                                            .offset(x = 8.dp, y = (-4).dp),
                                    ) {
                                        Badge {
                                            Text(if (unreadCount > 99) "99+" else unreadCount.toString())
                                        }
                                    }
                                }
                            }
                            Text(
                                text = destination.label,
                                fontSize = LabelSize,
                                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                                color = fg,
                                modifier = Modifier.padding(top = 2.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}
