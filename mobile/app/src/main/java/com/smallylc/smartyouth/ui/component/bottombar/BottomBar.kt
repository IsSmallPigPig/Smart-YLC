package com.smallylc.smartyouth.ui.component.bottombar

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
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
private val DockHeight = 72.dp
private val DockMargin = 26.dp
private val DockBottomFloat = 22.dp
/** 胶囊宽度占屏幕比例：比卡片窄一圈，图标不贴边。 */
private const val DockWidthFraction = 0.66f
private val ThumbHeight = 54.dp
private val ThumbTop = 9.dp
private val IconSize = 22.dp
/** 单个 tab 的固定宽度：整组居中，图标之间不被整条宽度拉开。 */
private val ItemWidth = 56.dp
private val LabelSize = 11.sp

/**
 * 悬浮胶囊底栏（仿 Classy）：
 * 选中项背后是一块会滑动的 thumb 色块，图标与文字按「thumb 覆盖率」渐变上色，
 * 切换 tab 时色块从旧座位弹到新座位，不是直接跳过去。
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
    var thumbWidthPx by remember { mutableFloatStateOf(0f) }
    var barX by remember { mutableFloatStateOf(0f) }
    var placed by remember { mutableStateOf(false) }
    val centers = remember { mutableStateListOf(*Array(items.size) { 0f }) }

    val thumbWidth = with(density) { thumbWidthPx.toDp() }
    val dockShape = RoundedCornerShape(percent = 50)
    val springSpec = spring<Float>(dampingRatio = 0.8f, stiffness = 380f)

    LaunchedEffect(centers.toList(), mainState.selectedPage, thumbWidthPx) {
        val index = mainState.selectedPage.coerceIn(0, items.lastIndex)
        if (thumbWidthPx <= 0f || centers[index] <= 0f) return@LaunchedEffect
        val target = centers[index] - barX - thumbWidthPx / 2f
        if (!placed) {
            thumbX.snapTo(target)
            placed = true
        } else {
            thumbX.animateTo(target, springSpec)
        }
    }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .navigationBarsPadding()
            .padding(bottom = DockBottomFloat),
        contentAlignment = Alignment.BottomCenter,
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth(DockWidthFraction)
                .height(DockHeight)
                .shadow(elevation = 14.dp, shape = dockShape, clip = false)
                .clip(dockShape)
                .background(colors.surfaceContainer.copy(alpha = 0.94f))
                .onGloballyPositioned { barX = it.positionInRoot().x },
        ) {
            if (thumbWidthPx > 0f) {
                Box(
                    modifier = Modifier
                        .padding(top = ThumbTop)
                        .offset { IntOffset(thumbX.value.roundToInt(), 0) }
                        .width(thumbWidth)
                        .height(ThumbHeight)
                        .clip(RoundedCornerShape(20.dp))
                        .background(colors.onSurface.copy(alpha = 0.09f)),
                )
            }
            Row(
                modifier = Modifier.fillMaxSize(),
                horizontalArrangement = Arrangement.Center,
            ) {
                items.forEachIndexed { index, destination ->
                    val center = centers.getOrElse(index) { 0f } - barX
                    val half = with(density) { IconSize.toPx() } / 2f
                    val coverage = if (thumbWidthPx <= 0f) {
                        if (mainState.selectedPage == index) 1f else 0f
                    } else {
                        intervalCoverage(
                            thumbX.value,
                            thumbX.value + thumbWidthPx,
                            center - half,
                            center + half,
                        )
                    }
                    val tint = lerp(
                        colors.onSurfaceVariantActions,
                        colors.onSurface,
                        coverage,
                    )
                    val interaction = remember { MutableInteractionSource() }
                    Box(
                        modifier = Modifier
                            .width(ItemWidth)
                            .fillMaxHeight()
                            .clickable(
                                interactionSource = interaction,
                                indication = null,
                            ) { mainState.animateToPage(index) }
                            .onGloballyPositioned {
                                centers[index] = it.positionInRoot().x + it.size.width / 2f
                                if (index == 0) {
                                    // 选中块比图标宽一圈：椭圆两端是弧、中间留一段直线，
                                    // 与图标等宽时会缩成小圆角方块
                                    thumbWidthPx = it.size.width + with(density) { 18.dp.toPx() }
                                }
                            },
                        contentAlignment = Alignment.Center,
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Box {
                                Icon(
                                    imageVector = destination.icon,
                                    contentDescription = destination.label,
                                    tint = tint,
                                    modifier = Modifier.size(IconSize),
                                )
                                if (destination == MainDestination.Messages && unreadCount > 0) {
                                    Box(
                                        modifier = Modifier
                                            .align(Alignment.TopEnd)
                                            .offset(x = 6.dp, y = (-4).dp),
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
                                fontWeight = if (mainState.selectedPage == index) FontWeight.SemiBold else FontWeight.Normal,
                                color = tint,
                                modifier = Modifier.padding(top = 2.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

/** 区间 [aStart,aEnd] 落在 [bStart,bEnd] 里的比例，用来让图标随色块滑动渐变上色。 */
private fun intervalCoverage(aStart: Float, aEnd: Float, bStart: Float, bEnd: Float): Float {
    val width = bEnd - bStart
    if (width <= 0f) return 0f
    val overlap = aEnd.coerceAtMost(bEnd) - aStart.coerceAtLeast(bStart)
    return (overlap / width).coerceIn(0f, 1f)
}
