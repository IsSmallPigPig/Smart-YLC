package com.smallylc.smartyouth.ui.screen.login

import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.data.LoginOutcome
import com.smallylc.smartyouth.data.Repo
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.ToneRed
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import kotlinx.coroutines.launch
import top.yukonga.miuix.kmp.basic.Button
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.IconButton
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TextField
import top.yukonga.miuix.kmp.basic.TextButton
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.basic.ButtonDefaults
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Back
import top.yukonga.miuix.kmp.overlay.OverlayDialog
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic

@Composable
fun LoginScreen(
    onBack: () -> Unit,
    onLoggedIn: () -> Unit,
    onCloudSync: () -> Unit,
) {
    val existing = Repo.accounts.active
    var userName by remember { mutableStateOf(existing?.userName.orEmpty()) }
    var password by remember { mutableStateOf(existing?.password.orEmpty()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var captcha by remember { mutableStateOf<android.graphics.Bitmap?>(null) }
    var captchaInput by remember { mutableStateOf("") }
    var captchaHint by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) Color.Transparent else colorScheme.surface

    fun submit(code: String = "") {
        if (loading) return
        if (userName.isBlank() || password.isBlank()) {
            error = "账号和密码都要填"
            return
        }
        loading = true
        error = null
        scope.launch {
            when (val outcome = Repo.login(userName.trim(), password, code)) {
                is LoginOutcome.Success -> {
                    loading = false
                    onLoggedIn()
                }
                is LoginOutcome.NeedCaptcha -> {
                    loading = false
                    captchaHint = outcome.message
                    captcha = Repo.fetchCaptcha()?.let {
                        android.graphics.BitmapFactory.decodeByteArray(it, 0, it.size)
                    }
                    if (captcha == null) error = "验证码加载失败，请重试"
                }
                is LoginOutcome.Failed -> {
                    loading = false
                    error = outcome.message
                }
            }
        }
    }

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = "登录支部账号",
                    color = barColor,
                    scrollBehavior = scrollBehavior,
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(
                                imageVector = MiuixIcons.Back,
                                contentDescription = "返回",
                                tint = colorScheme.onBackground,
                            )
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
            item { Spacer(Modifier.height(16.dp)) }
            item {
                Image(
                    painter = androidx.compose.ui.res.painterResource(com.smallylc.smartyouth.R.drawable.app_logo),
                    contentDescription = null,
                    modifier = Modifier
                        .size(72.dp)
                        .padding(start = 8.dp),
                )
            }
            item {
                Column(Modifier.padding(horizontal = 8.dp, vertical = 8.dp)) {
                    Text(
                        text = "智慧团委",
                        fontSize = 26.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = colorScheme.onBackground,
                    )
                    MutedText(
                        text = "用学校团委管理员账号登录，名单与上传都走这个账号的权限。",
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
            }
            item {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 12.dp),
                ) {
                    Column(Modifier.padding(12.dp)) {
                        TextField(
                            value = userName,
                            onValueChange = { userName = it },
                            label = "账号",
                            modifier = Modifier.fillMaxWidth(),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                            singleLine = true,
                        )
                        Spacer(Modifier.height(10.dp))
                        TextField(
                            value = password,
                            onValueChange = { password = it },
                            label = "密码",
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true,
                            visualTransformation = PasswordVisualTransformation(),
                            keyboardOptions = KeyboardOptions(
                                keyboardType = KeyboardType.Password,
                                imeAction = ImeAction.Done,
                            ),
                        )
                    }
                }
            }
            if (error != null) {
                item {
                    Text(
                        text = error!!,
                        fontSize = 13.sp,
                        color = ToneRed,
                        modifier = Modifier.padding(start = 16.dp, top = 8.dp),
                    )
                }
            }
            item {
                Button(
                    onClick = { submit() },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 16.dp)
                        .height(48.dp),
                    enabled = !loading,
                    colors = ButtonDefaults.buttonColorsPrimary(),
                ) {
                    Text(if (loading) "正在登录…" else "登录账号")
                }
            }
            item {
                MutedText(
                    text = "密码只保存在本机，不会上传到别处。",
                    modifier = Modifier.padding(top = 16.dp, start = 4.dp),
                )
            }
            item {
                Text(
                    text = "从云端同步配置",
                    fontSize = 14.sp,
                    color = colorScheme.primary,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 14.dp)
                        .clickable(onClick = onCloudSync)
                        .padding(vertical = 8.dp),
                    textAlign = TextAlign.Center,
                )
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }

    if (captcha != null) {
        OverlayDialog(
            show = true,
            title = "需要图形验证码",
            summary = captchaHint.ifBlank { "输入图片里的字符" },
            onDismissRequest = {
                captcha = null
                captchaInput = ""
            },
            content = {
                Column {
                    Image(
                        bitmap = captcha!!.asImageBitmap(),
                        contentDescription = "验证码",
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(72.dp)
                            .padding(bottom = 12.dp),
                    )
                    TextField(
                        value = captchaInput,
                        onValueChange = { captchaInput = it },
                        label = "验证码",
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                    )
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 16.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        TextButton(
                            text = "重新获取",
                            onClick = {
                                scope.launch {
                                    captcha = Repo.fetchCaptcha()?.let {
                                        android.graphics.BitmapFactory.decodeByteArray(it, 0, it.size)
                                    }
                                    captchaInput = ""
                                }
                            },
                            modifier = Modifier.weight(1f),
                        )
                        Spacer(Modifier.width(16.dp))
                        TextButton(
                            text = "确认提交",
                            onClick = {
                                val code = captchaInput.trim()
                                captcha = null
                                submit(code)
                            },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.textButtonColorsPrimary(),
                        )
                    }
                }
            },
        )
    }
}
