package com.smallylc.smartyouth.ui.component.bottombar

import androidx.compose.foundation.pager.PagerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.job
import kotlinx.coroutines.launch
import top.yukonga.miuix.kmp.basic.Badge
import top.yukonga.miuix.kmp.basic.NavigationBar
import top.yukonga.miuix.kmp.basic.NavigationBarItem
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Contacts
import top.yukonga.miuix.kmp.icon.extended.Home
import top.yukonga.miuix.kmp.icon.extended.Messages
import top.yukonga.miuix.kmp.icon.extended.Scan
import top.yukonga.miuix.kmp.icon.extended.Settings
import top.yukonga.miuix.kmp.theme.MiuixTheme
import top.yukonga.miuix.kmp.utils.springAnimateToPage

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

enum class MainDestination(val label: String, val icon: ImageVector) {
    Home("首页", MiuixIcons.Home),
    Roster("名单", MiuixIcons.Contacts),
    Scan("扫描", MiuixIcons.Scan),
    Messages("消息", MiuixIcons.Messages),
    Settings("我的", MiuixIcons.Settings),
}

@Composable
fun BottomBar(
    unreadCount: Int,
    mainState: MainPagerState,
    modifier: Modifier = Modifier,
) {
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    BlurredBar(backdrop) {
        NavigationBar(
            modifier = modifier,
            color = if (backdrop != null) Color.Transparent else MiuixTheme.colorScheme.surface,
        ) {
            MainDestination.entries.forEach { destination ->
                NavigationBarItem(
                    modifier = Modifier.weight(1f),
                    icon = destination.icon,
                    label = destination.label,
                    selected = mainState.selectedPage == destination.ordinal,
                    onClick = { mainState.animateToPage(destination.ordinal) },
                    badge = if (destination == MainDestination.Messages && unreadCount > 0) {
                        {
                            Badge {
                                Text(if (unreadCount > 99) "99+" else unreadCount.toString())
                            }
                        }
                    } else null,
                )
            }
        }
    }
}
