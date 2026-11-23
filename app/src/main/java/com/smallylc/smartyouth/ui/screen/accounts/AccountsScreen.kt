package com.smallylc.smartyouth.ui.screen.accounts

import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.smallylc.smartyouth.data.Account
import com.smallylc.smartyouth.data.CloudProfiles
import com.smallylc.smartyouth.data.LoginOutcome
import com.smallylc.smartyouth.data.Repo
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.EmptyHint
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.ToneGreen
import com.smallylc.smartyouth.ui.component.ToneRed
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import kotlinx.coroutines.launch
import top.yukonga.miuix.kmp.basic.Button
import top.yukonga.miuix.kmp.basic.ButtonDefaults
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.IconButton
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TextButton
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Add
import top.yukonga.miuix.kmp.icon.extended.Back
import top.yukonga.miuix.kmp.icon.extended.Delete
import top.yukonga.miuix.kmp.overlay.OverlayDialog
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic

/**
 * 账号管理：同步下来的账号统一列在这里，点一行就用保存的密码登录并切换，
 * 不再经过登录表单；只有站点要验证码时才跳回登录页补一步。
 */
@Composable
fun AccountsScreen(
    onBack: () -> Unit,
    onNeedLogin: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) Color.Transparent else colorScheme.surface
    val session by Repo.sessionState.collectAsStateWithLifecycle()

    var accounts by remember { mutableStateOf(Repo.accounts.accounts) }
    var switching by remember { mutableStateOf("") }
    var message by remember { mutableStateOf<String?>(null) }
    var messageOk by remember { mutableStateOf(false) }
    var pendingDelete by remember { mutableStateOf<Account?>(null) }

    fun switchTo(account: Account) {
        if (switching.isNotEmpty()) return
        if (account.userName == Repo.activeUserName && session.loggedIn) {
            message = "${account.userName} 已经登录"
            messageOk = true
            return
        }
        switching = account.userName
        message = null
        scope.launch {
            Repo.logout()
            when (val outcome = Repo.login(account.userName, account.password)) {
                is LoginOutcome.Success -> {
                    message = "已切换到 ${account.userName}"
                    messageOk = true
                }
                is LoginOutcome.NeedCaptcha -> {
                    message = "这个账号要验证码，去登录页补一步"
                    messageOk = false
                    onNeedLogin(account.userName)
                }
                is LoginOutcome.Failed -> {
                    message = outcome.message
                    messageOk = false
                }
                is LoginOutcome.Unavailable -> {
                    message = "站点正在维护，恢复后再切换"
                    messageOk = false
                }
            }
            switching = ""
        }
    }

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = if (accounts.isEmpty()) "账号管理" else "账号 ${accounts.size}",
                    color = barColor,
                    scrollBehavior = scrollBehavior,
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(MiuixIcons.Back, contentDescription = "返回", tint = colorScheme.onBackground)
                        }
                    },
                    actions = {
                        IconButton(onClick = { onNeedLogin("") }) {
                            Icon(MiuixIcons.Add, contentDescription = "添加账号", tint = colorScheme.onBackground)
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
            item { Spacer(Modifier.height(8.dp)) }

            CloudProfiles.active?.let { profile ->
                item {
                    MutedText(
                        "当前配置：${profile.name}",
                        Modifier.padding(start = 4.dp),
                    )
                }
            }

            message?.let { text ->
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Text(
                            text = text,
                            fontSize = 14.sp,
                            color = if (messageOk) ToneGreen else ToneRed,
                            maxLines = 5,
                            modifier = Modifier.padding(16.dp),
                        )
                    }
                }
                item { Spacer(Modifier.height(8.dp)) }
            }

            if (accounts.isEmpty()) {
                item { EmptyHint("还没有账号", "从云端同步，或手动添加一个支部账号。") }
                item {
                    Button(
                        onClick = { onNeedLogin("") },
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 16.dp)
                            .height(48.dp),
                        colors = ButtonDefaults.buttonColorsPrimary(),
                    ) {
                        Text("添加账号")
                    }
                }
            } else {
                items(accounts, key = { it.userName }) { account ->
                    val isCurrent = account.userName == Repo.activeUserName
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        onClick = { switchTo(account) },
                    ) {
                        Row(
                            modifier = Modifier.padding(start = 16.dp, end = 8.dp, top = 12.dp, bottom = 12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(
                                    text = account.userName,
                                    fontSize = 15.sp,
                                    fontWeight = FontWeight.Medium,
                                    color = colorScheme.onSurface,
                                    maxLines = 1,
                                )
                                MutedText(
                                    when {
                                        switching == account.userName -> "正在登录…"
                                        isCurrent && session.loggedIn -> "当前账号"
                                        isCurrent -> "会话失效，点一下重试"
                                        else -> account.remark.ifBlank { "点一下切换" }
                                    },
                                    Modifier.padding(top = 3.dp),
                                )
                            }
                            if (isCurrent && session.loggedIn) {
                                Text(
                                    text = "在线",
                                    fontSize = 12.sp,
                                    color = ToneGreen,
                                    modifier = Modifier.padding(end = 4.dp),
                                )
                            }
                            IconButton(onClick = { pendingDelete = account }) {
                                Icon(
                                    MiuixIcons.Delete,
                                    contentDescription = "删除账号",
                                    tint = ToneRed,
                                )
                            }
                        }
                    }
                    CardGap(8)
                }
            }

            item { Spacer(Modifier.height(24.dp)) }
        }
    }

    pendingDelete?.let { account ->
        OverlayDialog(
            show = true,
            title = "删除这个账号？",
            summary = account.userName,
            onDismissRequest = { pendingDelete = null },
            content = {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    TextButton(
                        text = "取消",
                        onClick = { pendingDelete = null },
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(
                        text = "删除",
                        onClick = {
                            Repo.accounts.remove(account.userName)
                            if (account.userName == Repo.activeUserName) Repo.logout()
                            accounts = Repo.accounts.accounts
                            pendingDelete = null
                        },
                        modifier = Modifier.weight(1f),
                        colors = ButtonDefaults.textButtonColors(color = ToneRed),
                    )
                }
            },
        )
    }
}
