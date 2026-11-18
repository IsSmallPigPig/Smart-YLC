package com.smallylc.smartyouth.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.PagerDefaults.flingBehavior
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigationevent.NavigationEventInfo
import androidx.navigationevent.compose.NavigationBackHandler
import androidx.navigationevent.compose.rememberNavigationEventState
import com.smallylc.smartyouth.data.Repo
import com.smallylc.smartyouth.scan.Naming
import com.smallylc.smartyouth.ui.component.bottombar.BottomBar
import com.smallylc.smartyouth.ui.component.bottombar.MainPagerState
import com.smallylc.smartyouth.ui.component.bottombar.NavigationRailMiuix
import com.smallylc.smartyouth.ui.component.bottombar.useNavigationRail
import com.smallylc.smartyouth.ui.component.bottombar.rememberMainPagerState
import com.smallylc.smartyouth.ui.navigation3.LocalNavigator
import com.smallylc.smartyouth.ui.navigation3.Navigator
import com.smallylc.smartyouth.ui.navigation3.Route
import com.smallylc.smartyouth.ui.navigation3.rememberNavigator
import com.smallylc.smartyouth.ui.screen.about.AboutScreen
import com.smallylc.smartyouth.ui.screen.home.HomeScreen
import com.smallylc.smartyouth.ui.screen.login.LoginScreen
import com.smallylc.smartyouth.ui.screen.members.MembersScreen
import com.smallylc.smartyouth.ui.screen.messages.MessagesScreen
import com.smallylc.smartyouth.ui.screen.scan.ScanScreen
import com.smallylc.smartyouth.ui.screen.scan.ScansScreen
import com.smallylc.smartyouth.ui.screen.settings.SettingsScreen
import com.smallylc.smartyouth.ui.screen.theme.ThemeScreen
import com.smallylc.smartyouth.ui.theme.LocalColorMode
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.theme.SmartYouthTheme
import com.smallylc.smartyouth.ui.viewmodel.MainActivityViewModel
import com.smallylc.smartyouth.ui.viewmodel.MainPagerConfig
import kotlinx.coroutines.launch
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.nav.core.NavDisplay
import top.yukonga.miuix.kmp.nav.core.NavDisplayEffects
import top.yukonga.miuix.kmp.nav.core.rememberNavSystemCornerRadius
import top.yukonga.miuix.kmp.nav.transition.NavSwipeDirection
import top.yukonga.miuix.kmp.utils.PagerGestureNestedScrollConnection
import top.yukonga.miuix.kmp.utils.PagerInterceptionMode
import top.yukonga.miuix.kmp.utils.PagerNavigationSpringSpec
import top.yukonga.miuix.kmp.utils.pagerGestureOverride

/** 从团员详情直接开扫描时，把姓名与筛选条件带过去。 */
object ScanIntent {
    var prefillName: String = ""
    var filter: String? = null
}

class MainActivity : ComponentActivity() {

    @SuppressLint("UnusedMaterial3ScaffoldPaddingParameter")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.auto(
                android.graphics.Color.TRANSPARENT,
                android.graphics.Color.TRANSPARENT,
            ) { false },
            navigationBarStyle = SystemBarStyle.auto(
                android.graphics.Color.TRANSPARENT,
                android.graphics.Color.TRANSPARENT,
            ) { false },
        )

        setContent {
            val viewModel = viewModel<MainActivityViewModel>()
            val uiState by viewModel.uiState.collectAsStateWithLifecycle()
            val selectedMainPage by viewModel.selectedMainPage.collectAsStateWithLifecycle()
            val appSettings = uiState.appSettings
            val darkMode = appSettings.colorMode.isDark ||
                (appSettings.colorMode.isSystem && isSystemInDarkTheme())

            DisposableEffect(darkMode) {
                enableEdgeToEdge(
                    statusBarStyle = SystemBarStyle.auto(
                        android.graphics.Color.TRANSPARENT,
                        android.graphics.Color.TRANSPARENT,
                    ) { darkMode },
                    navigationBarStyle = SystemBarStyle.auto(
                        android.graphics.Color.TRANSPARENT,
                        android.graphics.Color.TRANSPARENT,
                    ) { darkMode },
                )
                window.isNavigationBarContrastEnforced = false
                onDispose { }
            }

            val navigator = rememberNavigator(Route.Main)
            val systemDensity = LocalDensity.current
            val density = remember(systemDensity, uiState.pageScale) {
                androidx.compose.ui.unit.Density(systemDensity.density * uiState.pageScale, systemDensity.fontScale)
            }

            LaunchedEffect(Unit) { Repo.restore() }

            CompositionLocalProvider(
                LocalNavigator provides navigator,
                LocalDensity provides density,
                LocalColorMode provides appSettings.colorMode.value,
                LocalEnableBlur provides uiState.enableBlur,
            ) {
                SmartYouthTheme(appSettings = appSettings, uiMode = UiMode.Miuix) {
                    val navDisplay = @Composable {
                        NavDisplay(
                            backStack = navigator.backStack,
                            effects = NavDisplayEffects(cornerClipRadius = rememberNavSystemCornerRadius()),
                            onBack = { navigator.pop() },
                        ) {
                            entry<Route.Main>(swipeDismiss = NavSwipeDirection.None) {
                                MainScreen(
                                    initialPage = selectedMainPage,
                                    onPageChanged = viewModel::setSelectedMainPage,
                                    onOpenLogin = { navigator.push(Route.Login) },
                                    onOpenScan = {
                                        ScanIntent.prefillName = ""
                                        navigator.push(Route.Scan)
                                    },
                                    onOpenTheme = { navigator.push(Route.Theme) },
                                    onOpenAbout = { navigator.push(Route.About) },
                                )
                            }
                            entry<Route.Login>(swipeDismiss = NavSwipeDirection.LeftToRight) {
                                LoginScreen(
                                    onBack = { navigator.pop() },
                                    onLoggedIn = { navigator.pop() },
                                )
                            }
                            entry<Route.Scan>(swipeDismiss = NavSwipeDirection.None) {
                                ScanScreen(
                                    prefillName = ScanIntent.prefillName,
                                    onExit = { navigator.pop() },
                                )
                            }
                            entry<Route.Theme>(swipeDismiss = NavSwipeDirection.LeftToRight) {
                                ThemeScreen(onBack = { navigator.pop() })
                            }
                            entry<Route.About>(swipeDismiss = NavSwipeDirection.LeftToRight) {
                                AboutScreen(onBack = { navigator.pop() })
                            }
                        }
                    }
                    Scaffold { navDisplay() }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
    }
}

val LocalMainPagerState = staticCompositionLocalOf<MainPagerState> { error("LocalMainPagerState not provided") }

@SuppressLint("UnusedMaterial3ScaffoldPaddingParameter")
@Composable
fun MainScreen(
    initialPage: Int = 0,
    onPageChanged: (Int) -> Unit = {},
    onOpenLogin: () -> Unit,
    onOpenScan: () -> Unit,
    onOpenTheme: () -> Unit,
    onOpenAbout: () -> Unit,
) {
    val navigator = LocalNavigator.current
    val wideScreen = useNavigationRail()
    val pagerState = rememberPagerState(initialPage = initialPage, pageCount = { MainPagerConfig.PAGE_COUNT })
    val mainPagerState = rememberMainPagerState(pagerState, animatePageChanges = !wideScreen)
    val pagerMode = PagerInterceptionMode.CrossAxisInterceptor

    val session by Repo.sessionState.collectAsStateWithLifecycle()
    val members by Repo.members.collectAsStateWithLifecycle()
    val messages by Repo.messages.collectAsStateWithLifecycle()
    val unreadCount by remember { derivedStateOf { messages.count { it.unread } } }
    val sync by Repo.syncState.collectAsStateWithLifecycle()
    val overview = remember(members, messages) { Repo.overview() }
    val scope = rememberCoroutineScope()

    val settledPage = pagerState.settledPage
    LaunchedEffect(settledPage) { onPageChanged(settledPage) }
    val currentPage = pagerState.currentPage
    LaunchedEffect(currentPage) { mainPagerState.syncPage() }

    MainScreenBackHandler(mainPagerState, navigator)

    CompositionLocalProvider(LocalMainPagerState provides mainPagerState) {
        val pager = @Composable { bottom: androidx.compose.ui.unit.Dp ->
            HorizontalPager(
                modifier = Modifier
                    .pagerGestureOverride(pagerState = mainPagerState.pagerState, mode = pagerMode)
                    .then(if (wideScreen) Modifier.widthIn(max = 1100.dp) else Modifier),
                state = mainPagerState.pagerState,
                beyondViewportPageCount = 1,
                overscrollEffect = null,
                userScrollEnabled = false,
                pageNestedScrollConnection = PagerGestureNestedScrollConnection,
                flingBehavior = flingBehavior(
                    state = mainPagerState.pagerState,
                    snapAnimationSpec = PagerNavigationSpringSpec,
                ),
            ) { page ->
                when (page) {
                    0 -> HomeScreen(
                        loggedIn = session.loggedIn,
                        userName = session.userName,
                        overview = overview,
                        syncing = sync.syncing,
                        lastSync = sync.lastSync,
                        error = sync.error,
                        bottomPadding = bottom,
                        onLogin = onOpenLogin,
                        onScan = onOpenScan,
                        onSync = { scope.launch { Repo.refreshAll() } },
                        onOpenRoster = { filter ->
                            ScanIntent.filter = filter
                            mainPagerState.animateToPage(1)
                        },
                        onOpenMessages = { mainPagerState.animateToPage(3) },
                    )

                    1 -> MembersScreen(
                        loggedIn = session.loggedIn,
                        members = members,
                        initialFilter = ScanIntent.filter,
                        bottomPadding = bottom,
                        onLogin = onOpenLogin,
                        onScanFor = { member ->
                            ScanIntent.prefillName = Naming.fileNameFor(member)
                            navigator.push(Route.Scan)
                        },
                    )

                    2 -> ScansScreen(
                        bottomPadding = bottom,
                        onNewScan = {
                            ScanIntent.prefillName = ""
                            navigator.push(Route.Scan)
                        },
                    )

                    3 -> MessagesScreen(
                        loggedIn = session.loggedIn,
                        messages = messages,
                        syncing = sync.syncing,
                        error = sync.error,
                        bottomPadding = bottom,
                        onLogin = onOpenLogin,
                    )

                    4 -> SettingsScreen(
                        userName = session.userName,
                        loggedIn = session.loggedIn,
                        syncing = sync.syncing,
                        memberCount = members.size,
                        bottomPadding = bottom,
                        onLogin = onOpenLogin,
                        onLogout = { Repo.logout() },
                        onOpenTheme = onOpenTheme,
                        onOpenAbout = onOpenAbout,
                        onSync = { scope.launch { Repo.refreshAll() } },
                    )
                }
            }
        }

        if (wideScreen) {
            Scaffold { _ ->
                Row {
                    NavigationRailMiuix(
                        unreadCount = unreadCount,
                        mainState = mainPagerState,
                    )
                    Box(Modifier.weight(1f)) {
                        pager(0.dp)
                    }
                }
            }
        } else {
            Scaffold(
                bottomBar = {
                    Box(modifier = Modifier.fillMaxWidth()) {
                        BottomBar(
                            unreadCount = unreadCount,
                            mainState = mainPagerState,
                            modifier = Modifier.align(Alignment.BottomCenter),
                        )
                    }
                },
            ) { innerPadding -> pager(innerPadding.calculateBottomPadding()) }
        }
    }
}

@Composable
private fun MainScreenBackHandler(
    mainState: MainPagerState,
    navController: Navigator,
) {
    val isPagerBackHandlerEnabled by remember {
        derivedStateOf {
            navController.current() is Route.Main &&
                navController.backStackSize() == 1 &&
                mainState.selectedPage != 0
        }
    }
    val navEventState = rememberNavigationEventState(NavigationEventInfo.None)

    NavigationBackHandler(
        state = navEventState,
        isBackEnabled = isPagerBackHandlerEnabled,
        onBackCompleted = { mainState.animateToPage(0) },
    )
}
