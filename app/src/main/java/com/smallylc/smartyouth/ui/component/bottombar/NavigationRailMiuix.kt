package com.smallylc.smartyouth.ui.component.bottombar

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import com.smallylc.smartyouth.data.repository.SettingsRepositoryImpl
import top.yukonga.miuix.kmp.basic.Badge
import top.yukonga.miuix.kmp.basic.NavigationRail
import top.yukonga.miuix.kmp.basic.NavigationRailItem
import top.yukonga.miuix.kmp.basic.NavigationRailValue
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.rememberNavigationRailState
import top.yukonga.miuix.kmp.theme.MiuixTheme

/** 平板竖屏/横屏的侧边导航：宽屏时替代底部栏，未读消息用角标提示。 */
@Composable
fun NavigationRailMiuix(
    unreadCount: Int,
    mainState: MainPagerState,
    modifier: Modifier = Modifier,
) {
    val repo = remember { SettingsRepositoryImpl() }
    val state = rememberNavigationRailState(
        initialValue = if (repo.navigationRailExpanded) NavigationRailValue.Expanded else NavigationRailValue.Collapsed,
    )
    LaunchedEffect(state.currentValue) { repo.navigationRailExpanded = state.isExpanded }

    NavigationRail(
        modifier = modifier,
        state = state,
        color = MiuixTheme.colorScheme.surface,
        expandContentDescription = "展开导航",
        collapseContentDescription = "收起导航",
    ) {
        MainDestination.entries.forEachIndexed { index, destination ->
            NavigationRailItem(
                selected = mainState.selectedPage == index,
                onClick = { mainState.animateToPage(index) },
                icon = destination.icon,
                label = destination.label,
                badge = if (destination == MainDestination.Messages && unreadCount > 0) {
                    {
                        Badge { Text(if (unreadCount > 99) "99+" else unreadCount.toString()) }
                    }
                } else null,
            )
        }
    }
}
