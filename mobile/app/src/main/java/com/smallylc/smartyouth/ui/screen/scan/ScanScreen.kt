package com.smallylc.smartyouth.ui.screen.scan

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
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
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.displayCutout
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.data.Repo
import com.smallylc.smartyouth.data.ScanPrefs
import com.smallylc.smartyouth.scan.CameraCapture
import com.smallylc.smartyouth.scan.CameraHandle
import com.smallylc.smartyouth.scan.CropEditor
import com.smallylc.smartyouth.scan.DocStore
import com.smallylc.smartyouth.scan.DocDetect
import com.smallylc.smartyouth.scan.Naming
import com.smallylc.smartyouth.scan.ScanPage
import com.smallylc.smartyouth.scan.ScanSession
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.EmptyHint
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
import top.yukonga.miuix.kmp.icon.extended.Add
import top.yukonga.miuix.kmp.icon.extended.Back
import top.yukonga.miuix.kmp.icon.extended.Delete
import top.yukonga.miuix.kmp.icon.extended.Edit
import top.yukonga.miuix.kmp.icon.extended.Photos
import top.yukonga.miuix.kmp.icon.extended.RotateLeft
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic

private enum class ScanStep { Capture, Review, Crop, Save }

/**
 * 扫描流程：取景 → 多页 → 逐页调整 → 命名保存。
 * 每一步都只有一个主动作，返回键按步骤后退，不直接丢掉已拍的东西。
 */
@Composable
fun ScanScreen(
    prefillName: String,
    onExit: () -> Unit,
) {
    var step by remember { mutableStateOf(ScanStep.Capture) }
    var editingId by remember { mutableStateOf<Long?>(null) }
    var camera by remember { mutableStateOf<CameraHandle?>(null) }
    var flashMessage by remember { mutableStateOf<String?>(null) }
    val pages by ScanSession.pages.collectAsState()
    val scope = rememberCoroutineScope()

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) {
            scope.launch {
                ScanSession.importFromUri(uri) ?: run { flashMessage = "这张图片读不出来" }
            }
        }
    }

    LaunchedEffect(Unit) {
        if (ScanSession.isEmpty) {
            ScanSession.startFresh()
        }
    }

    val goBack: () -> Unit = {
        when (step) {
            ScanStep.Capture -> onExit()
            ScanStep.Review -> step = ScanStep.Capture
            ScanStep.Crop -> {
                editingId = null
                step = ScanStep.Review
            }
            ScanStep.Save -> step = ScanStep.Review
        }
    }

    when (step) {
        ScanStep.Capture -> CaptureStep(
            pages = pages,
            camera = camera,
            onCameraReady = { camera = it },
            flashMessage = flashMessage,
            onDismissMessage = { flashMessage = null },
            onCapture = { file ->
                scope.launch {
                    runCatching {
                        val page = ScanSession.addFromFile(file)
                        ScanSession.updateFilter(page.id, currentFilter())
                    }.onFailure { flashMessage = "这一张没处理成功：${it.message}" }
                }
            },
            onCaptureError = { flashMessage = it },
            onImport = {
                picker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
            },
            onDone = {
                if (pages.isEmpty()) {
                    onExit()
                } else {
                    step = ScanStep.Review
                }
            },
            onExit = onExit,
        )

        ScanStep.Review -> ReviewStep(
            pages = pages,
            onBack = { step = ScanStep.Capture },
            onEdit = { page ->
                editingId = page.id
                step = ScanStep.Crop
            },
            onDelete = { ScanSession.delete(it.id) },
            onNext = { step = ScanStep.Save },
            onAdd = { step = ScanStep.Capture },
        )

        ScanStep.Crop -> {
            val page = pages.firstOrNull { it.id == editingId }
            if (page == null) {
                step = ScanStep.Review
            } else {
                CropStep(
                    page = page,
                    onBack = {
                        editingId = null
                        step = ScanStep.Review
                    },
                    onRotate = { scope.launch { ScanSession.rotate(page.id) } },
                    onDelete = {
                        ScanSession.delete(page.id)
                        editingId = null
                        step = ScanStep.Review
                    },
                    onChangeQuad = { ScanSession.updateQuad(page.id, it) },
                    onChangeFilter = { ScanSession.updateFilter(page.id, it) },
                )
            }
        }

        ScanStep.Save -> SaveStep(
            pages = pages,
            prefillName = prefillName,
            onBack = { step = ScanStep.Review },
            onExit = onExit,
        )
    }
}

private fun currentFilter(): DocDetect.Filter = when (ScanPrefs.defaultFilter) {
    "BW" -> DocDetect.Filter.BW
    "ORIGINAL" -> DocDetect.Filter.ORIGINAL
    else -> DocDetect.Filter.ENHANCE
}

// ---------- 取景 ----------

@Composable
private fun CaptureStep(
    pages: List<ScanPage>,
    camera: CameraHandle?,
    onCameraReady: (CameraHandle) -> Unit,
    flashMessage: String?,
    onDismissMessage: () -> Unit,
    onCapture: (java.io.File) -> Unit,
    onCaptureError: (String) -> Unit,
    onImport: () -> Unit,
    onDone: () -> Unit,
    onExit: () -> Unit,
) {
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black),
    ) {
        CameraCapture(Modifier.fillMaxSize(), onReady = onCameraReady)

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(140.dp)
                .align(Alignment.TopCenter)
                .background(
                    Brush.verticalGradient(
                        listOf(Color.Black.copy(alpha = 0.55f), Color.Transparent),
                    )
                ),
        )

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .statusBarsPadding()
                .padding(top = 4.dp, start = 8.dp, end = 8.dp)
                .align(Alignment.TopCenter),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .clip(CircleShape)
                    .background(Color.Black.copy(alpha = 0.35f)),
                contentAlignment = Alignment.Center,
            ) {
                IconButton(onClick = onExit) {
                    Icon(MiuixIcons.Back, contentDescription = "退出", tint = Color.White)
                }
            }
            Spacer(Modifier.weight(1f))
            if (pages.isNotEmpty()) {
                Text(
                    text = "已拍 ${pages.size} 页",
                    color = Color.White,
                    fontSize = 14.sp,
                    modifier = Modifier
                        .background(Color.Black.copy(alpha = 0.4f), RoundedCornerShape(14.dp))
                        .padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }

        // 取景框：贴着画面画出纸张大致范围，提示对齐
        Box(
            modifier = Modifier
                .fillMaxWidth(0.86f)
                .aspectRatio(0.72f)
                .align(Alignment.Center)
                .border(1.dp, Color.White.copy(alpha = 0.55f), RoundedCornerShape(8.dp)),
        )

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(250.dp)
                .align(Alignment.BottomCenter)
                .background(
                    Brush.verticalGradient(
                        listOf(Color.Transparent, Color.Black.copy(alpha = 0.6f)),
                    )
                ),
        )

        Column(
            modifier = Modifier
                .fillMaxWidth()
                .align(Alignment.BottomCenter)
                .navigationBarsPadding()
                .padding(bottom = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            if (pages.isNotEmpty()) {
                LazyRow(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(pages, key = { it.id }) { page ->
                        PageThumb(page, height = 64)
                    }
                }
                Spacer(Modifier.height(14.dp))
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceEvenly,
            ) {
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .clip(CircleShape)
                        .background(Color.Black.copy(alpha = 0.35f)),
                    contentAlignment = Alignment.Center,
                ) {
                    IconButton(onClick = onImport) {
                        Icon(MiuixIcons.Photos, contentDescription = "从相册导入", tint = Color.White)
                    }
                }

                Box(
                    modifier = Modifier
                        .size(76.dp)
                        .clip(CircleShape)
                        .background(Color.Black.copy(alpha = 0.28f))
                        .border(3.dp, Color.White, CircleShape)
                        .clickable(enabled = camera != null) {
                            camera?.shoot(
                                onSaved = onCapture,
                                onError = onCaptureError,
                            )
                        },
                ) {
                    Box(
                        Modifier
                            .fillMaxSize()
                            .padding(6.dp)
                            .clip(CircleShape)
                            .background(Color.White.copy(alpha = 0.9f)),
                    )
                }

                if (pages.isEmpty()) {
                    Spacer(Modifier.size(48.dp))
                } else {
                    TextButton(
                        text = "完成拍摄",
                        onClick = onDone,
                        colors = ButtonDefaults.textButtonColorsPrimary(),
                    )
                }
            }

            if (flashMessage != null) {
                Text(
                    text = flashMessage,
                    color = Color.White,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(top = 10.dp),
                )
            }
        }
    }
}

// ---------- 页列表 ----------

@Composable
private fun ReviewStep(
    pages: List<ScanPage>,
    onBack: () -> Unit,
    onEdit: (ScanPage) -> Unit,
    onDelete: (ScanPage) -> Unit,
    onNext: () -> Unit,
    onAdd: () -> Unit,
) {
    val scrollBehavior = MiuixScrollBehavior()
    Scaffold(
        topBar = {
            TopAppBar(
                title = "已拍 ${pages.size} 页",
                scrollBehavior = scrollBehavior,
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(MiuixIcons.Back, contentDescription = "返回拍摄", tint = colorScheme.onBackground)
                    }
                },
            )
        },
        popupHost = { },
        contentWindowInsets = WindowInsets.systemBars.add(WindowInsets.displayCutout)
            .only(WindowInsetsSides.Horizontal),
        bottomBar = {
            Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
                MutedText("点页面可重新裁边、换滤镜或删除。")
                CardGap(8)
                Button(
                    onClick = onNext,
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColorsPrimary(),
                ) {
                    Text("保存档案")
                }
            }
        },
    ) { innerPadding ->
        if (pages.isEmpty()) {
            EmptyHint("还没有页面", "回到取景框拍第一张，或从相册导入。")
        } else {
            LazyVerticalGrid(
                columns = GridCells.Fixed(3),
                modifier = Modifier
                    .fillMaxHeight()
                    .padding(horizontal = 12.dp),
                contentPadding = innerPadding,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(pages, key = { it.id }) { page ->
                    Column {
                        Box {
                            PageThumb(page, aspect = 0.72f, fillWidth = true)
                            Text(
                                text = "${pages.indexOf(page) + 1}",
                                color = Color.White,
                                fontSize = 11.sp,
                                modifier = Modifier
                                    .padding(6.dp)
                                    .background(Color.Black.copy(alpha = 0.45f), RoundedCornerShape(6.dp))
                                    .padding(horizontal = 5.dp, vertical = 1.dp),
                            )
                        }
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            IconButton(onClick = { onEdit(page) }) {
                                Icon(
                                    MiuixIcons.Edit,
                                    contentDescription = "调整这一页",
                                    tint = colorScheme.primary,
                                )
                            }
                            IconButton(onClick = { onDelete(page) }) {
                                Icon(
                                    MiuixIcons.Delete,
                                    contentDescription = "删掉这一页",
                                    tint = colorScheme.onSurfaceVariantSummary,
                                )
                            }
                        }
                    }
                }
                item {
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .aspectRatio(0.72f),
                        onClick = onAdd,
                        cornerRadius = 10.dp,
                    ) {
                        Column(
                            modifier = Modifier.fillMaxSize(),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.Center,
                        ) {
                            Icon(MiuixIcons.Add, contentDescription = null, tint = colorScheme.primary)
                            MutedText("继续拍摄", Modifier.padding(top = 4.dp))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PageThumb(page: ScanPage, height: Int = 0, aspect: Float? = null, fillWidth: Boolean = false) {
    val bitmap by produceState<android.graphics.Bitmap?>(null, page.id, page.quad, page.filter) {
        value = ScanSession.render(page, 300)
    }
    val modifier = when {
        aspect != null -> Modifier.fillMaxWidth().aspectRatio(aspect)
        fillWidth -> Modifier.fillMaxWidth().height(height.dp)
        else -> Modifier.height(height.dp).width((height * 0.72f).dp)
    }
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(6.dp))
            .background(Color(0xFF2A2A2A)),
        contentAlignment = Alignment.Center,
    ) {
        bitmap?.let {
            Image(
                bitmap = it.asImageBitmap(),
                contentDescription = null,
                contentScale = ContentScale.Fit,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

// ---------- 单页调整 ----------

@Composable
private fun CropStep(
    page: ScanPage,
    onBack: () -> Unit,
    onRotate: () -> Unit,
    onDelete: () -> Unit,
    onChangeQuad: (DocDetect.Quad) -> Unit,
    onChangeFilter: (DocDetect.Filter) -> Unit,
) {
    val pages by ScanSession.pages.collectAsState()
    val index = pages.indexOfFirst { it.id == page.id } + 1
    Scaffold(
        topBar = {
            TopAppBar(
                title = "第 $index 页",
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(MiuixIcons.Back, contentDescription = "返回", tint = colorScheme.onBackground)
                    }
                },
                actions = {
                    IconButton(onClick = onRotate) {
                        Icon(MiuixIcons.RotateLeft, contentDescription = "旋转", tint = colorScheme.onBackground)
                    }
                    IconButton(onClick = onDelete) {
                        Icon(MiuixIcons.Delete, contentDescription = "删除这一页", tint = colorScheme.onBackground)
                    }
                },
            )
        },
        popupHost = { },
        contentWindowInsets = WindowInsets.systemBars.add(WindowInsets.displayCutout)
            .only(WindowInsetsSides.Horizontal),
    ) { innerPadding ->
        Column(
            Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            CropEditor(
                page = page,
                onChangeQuad = onChangeQuad,
                onChangeFilter = onChangeFilter,
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
            )
            Button(
                onClick = onBack,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                colors = ButtonDefaults.buttonColorsPrimary(),
            ) {
                Text("完成调整")
            }
        }
    }
}
