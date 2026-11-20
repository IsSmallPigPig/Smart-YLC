package com.smallylc.smartyouth.ui.screen.pair

import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.smallylc.smartyouth.scan.DocStore
import com.smallylc.smartyouth.scan.QrAnalyzer
import com.smallylc.smartyouth.site.PairClient
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.ToneGreen
import com.smallylc.smartyouth.ui.component.ToneRed
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import top.yukonga.miuix.kmp.basic.Button
import top.yukonga.miuix.kmp.basic.ButtonDefaults
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.IconButton
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TextField
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Back
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic
import java.util.concurrent.Executors

private enum class PairStep { Scan, Manual, Transfer }

/**
 * 传到电脑：扫电脑上的二维码配对，只传电脑还没有的档案。
 * 协议见 docs/app-pair-protocol.md；手输入口给相机不方便或二维码过期的场景兜底。
 */
@Composable
fun PairScreen(
    deviceName: String,
    onBack: () -> Unit,
) {
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) androidx.compose.ui.graphics.Color.Transparent else colorScheme.surface

    var step by remember { mutableStateOf(PairStep.Scan) }
    var host by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var configCode by remember { mutableStateOf("") }
    var message by remember { mutableStateOf<String?>(null) }
    var messageOk by remember { mutableStateOf(false) }
    var paired by remember { mutableStateOf<PairClient?>(null) }
    var token by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var total by remember { mutableIntStateOf(0) }
    var doneCount by remember { mutableIntStateOf(0) }
    val results = remember { mutableStateListOf<Pair<String, Boolean>>() }

    fun connect(client: PairClient) {
        if (busy) return
        busy = true
        message = null
        scope.launch(Dispatchers.IO) {
            when (val outcome = client.pair(deviceName)) {
                is PairClient.Result.Fail -> withContext(Dispatchers.Main) {
                    message = outcome.message
                    messageOk = false
                    busy = false
                    if (outcome.kind == "config") {
                        host = client.base.removePrefix("http://")
                        pin = client.pin
                        step = PairStep.Manual
                    }
                }

                is PairClient.Result.Ok -> {
                    val session = outcome.value
                    val known = client.manifest(session)
                    val local = DocStore.list()
                    val todo = local.filter { PairClient.sha256(it.file) !in known }
                    withContext(Dispatchers.Main) {
                        paired = client
                        token = session
                        total = todo.size
                        doneCount = 0
                        results.clear()
                        step = PairStep.Transfer
                        busy = false
                        message = if (todo.isEmpty()) "电脑上已经是最新的" else null
                        messageOk = true
                    }
                    todo.forEach { doc ->
                        val result = runCatching { client.upload(session, doc.file) }
                            .getOrElse { PairClient.Result.Fail(it.message ?: "上传中断") }
                        withContext(Dispatchers.Main) {
                            doneCount += 1
                            results.add(
                                when (result) {
                                    is PairClient.Result.Ok -> "${doc.name}：${result.value}" to true
                                    is PairClient.Result.Fail -> "${doc.name}：${result.message}" to false
                                }
                            )
                        }
                    }
                    client.done(session)
                    withContext(Dispatchers.Main) {
                        message = if (todo.isEmpty()) "电脑上已经是最新的" else "传完了，共 ${todo.size} 份"
                        messageOk = true
                    }
                }
            }
        }
    }

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = "传到电脑",
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
                .nestedScroll(scrollBehavior.nestedScrollConnection),
            contentPadding = innerPadding,
            overscrollEffect = null,
        ) {
            when (step) {
                PairStep.Scan -> {
                    item {
                        Card(Modifier.fillMaxWidth().padding(12.dp)) {
                            Column(Modifier.padding(14.dp)) {
                                Box(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(320.dp)
                                        .clip(RoundedCornerShape(18.dp))
                                        .background(colorScheme.surfaceContainerHighest)
                                        .border(1.dp, colorScheme.outline, RoundedCornerShape(18.dp)),
                                ) {
                                    ScannerPreview(
                                        modifier = Modifier.fillMaxSize(),
                                        onQr = { text ->
                                            if (!busy) {
                                                val client = PairClient.parseQr(text)
                                                if (client == null) {
                                                    message = "这个码不是电脑上的配对码"
                                                    messageOk = false
                                                } else {
                                                    connect(client)
                                                }
                                            }
                                        },
                                    )
                                }
                                MutedText(
                                    "电脑接收页的二维码",
                                    Modifier.padding(top = 12.dp),
                                )
                                Row(
                                    modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
                                    horizontalArrangement = Arrangement.End,
                                ) {
                                    top.yukonga.miuix.kmp.basic.TextButton(
                                        text = "手动配对",
                                        onClick = { step = PairStep.Manual },
                                        colors = ButtonDefaults.textButtonColors(color = colorScheme.primary),
                                    )
                                }
                            }
                        }
                    }
                }

                PairStep.Manual -> {
                    item {
                        Card(Modifier.fillMaxWidth().padding(12.dp)) {
                            Column(Modifier.padding(14.dp)) {
                                TextField(
                                    value = host,
                                    onValueChange = { host = it; message = null },
                                    label = "电脑地址",
                                    modifier = Modifier.fillMaxWidth(),
                                    singleLine = true,
                                )
                                TextField(
                                    value = pin,
                                    onValueChange = { pin = it; message = null },
                                    label = "配对码",
                                    modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                                    singleLine = true,
                                )
                                TextField(
                                    value = configCode,
                                    onValueChange = { configCode = it; message = null },
                                    label = "配置提取码（可选）",
                                    modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
                                    singleLine = true,
                                )
                                Button(
                                    onClick = {
                                        val address = host.trim().let {
                                            if (it.startsWith("http")) it else "http://$it"
                                        }
                                        val client = PairClient(
                                            address.trimEnd('/'),
                                            pin.trim().uppercase(),
                                            configCode.trim().uppercase(),
                                        )
                                        connect(client)
                                    },
                                    enabled = !busy,
                                    modifier = Modifier.fillMaxWidth().padding(top = 14.dp),
                                    colors = ButtonDefaults.buttonColorsPrimary(),
                                ) {
                                    Text(if (busy) "正在配对…" else "开始配对")
                                }
                            }
                        }
                    }
                }

                PairStep.Transfer -> {
                    item {
                        Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                            Column(Modifier.padding(16.dp)) {
                                Text(
                                    text = if (total == 0) "没有要传的档案" else "已传 $doneCount / $total",
                                    fontSize = 17.sp,
                                    color = colorScheme.onSurface,
                                )
                                MutedText("电脑上已有的会按内容跳过", Modifier.padding(top = 4.dp))
                            }
                        }
                    }
                    items(results) { (line, ok) ->
                        Card(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
                            Text(
                                text = line,
                                fontSize = 13.sp,
                                color = if (ok) ToneGreen else ToneRed,
                                modifier = Modifier.padding(14.dp),
                            )
                        }
                    }
                    item {
                        CardGap(12)
                        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                            Button(
                                onClick = {
                                    step = PairStep.Scan
                                    results.clear()
                                    total = 0
                                    doneCount = 0
                                    message = null
                                },
                                modifier = Modifier.fillMaxWidth(),
                                colors = ButtonDefaults.buttonColorsPrimary(),
                            ) {
                                Text("再传一次")
                            }
                        }
                    }
                }
            }

            message?.let { text ->
                item {
                    Card(Modifier.fillMaxWidth().padding(12.dp)) {
                        Text(
                            text = text,
                            fontSize = 13.sp,
                            color = if (messageOk) ToneGreen else ToneRed,
                            modifier = Modifier.padding(14.dp),
                        )
                    }
                }
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }
}

/** CameraX 预览 + 帧分析；退出页面时解绑，避免相机被占住。 */
@Composable
private fun ScannerPreview(modifier: Modifier = Modifier, onQr: (String) -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val currentOnQr = rememberUpdatedState(onQr)
    val previewView = remember {
        PreviewView(context).apply { scaleType = PreviewView.ScaleType.FILL_CENTER }
    }
    val executor = remember { Executors.newSingleThreadExecutor() }
    val analyzer = remember(context) {
        QrAnalyzer { text -> context.mainExecutor.execute { currentOnQr.value(text) } }
    }

    DisposableEffect(Unit) {
        val future = ProcessCameraProvider.getInstance(context)
        val listener = Runnable {
            val provider = runCatching { future.get() }.getOrNull() ?: return@Runnable
            val preview = Preview.Builder().build().also {
                it.surfaceProvider = previewView.surfaceProvider
            }
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
                .also { it.setAnalyzer(executor, analyzer) }
            runCatching {
                provider.unbindAll()
                provider.bindToLifecycle(
                    lifecycleOwner,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    analysis,
                )
            }
        }
        future.addListener(listener, ContextCompat.getMainExecutor(context))
        onDispose {
            runCatching { ProcessCameraProvider.getInstance(context).get().unbindAll() }
            executor.shutdown()
        }
    }

    AndroidView(factory = { previewView }, modifier = modifier)
}
