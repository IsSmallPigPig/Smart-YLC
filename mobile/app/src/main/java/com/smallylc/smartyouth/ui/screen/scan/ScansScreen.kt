package com.smallylc.smartyouth.ui.screen.scan

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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.scan.DocStore
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.EmptyHint
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.ToneRed
import com.smallylc.smartyouth.ui.theme.LocalEnableBlur
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Delete
import top.yukonga.miuix.kmp.icon.extended.Share
import com.smallylc.smartyouth.ui.util.BlurredBar
import com.smallylc.smartyouth.ui.util.rememberBlurBackdrop
import top.yukonga.miuix.kmp.basic.Button
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.IconButton
import top.yukonga.miuix.kmp.basic.TextButton
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.overlay.OverlayDialog
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 扫描页：本机扫描件的落脚点，同时是新建扫描的入口。 */
@Composable
fun ScansScreen(
    bottomPadding: Dp,
    onNewScan: () -> Unit,
) {
    var docs by remember { mutableStateOf(DocStore.list()) }
    var pendingDelete by remember { mutableStateOf<DocStore.SavedDoc?>(null) }
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) Color.Transparent else colorScheme.surface

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = if (docs.isEmpty()) "扫描" else "扫描件 ${docs.size}",
                    color = barColor,
                    scrollBehavior = scrollBehavior,
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
                .padding(horizontal = 12.dp),
            contentPadding = innerPadding,
            overscrollEffect = null,
        ) {
            item { Spacer(Modifier.height(8.dp)) }
            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        Text(
                            text = "拍纸质入团志愿书",
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Medium,
                            color = colorScheme.onSurface,
                        )
                        MutedText(
                            "自动裁边摆正，多页合成一个 PDF。",
                            Modifier.padding(top = 6.dp),
                        )
                        Button(
                            onClick = onNewScan,
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(top = 14.dp),
                            colors = top.yukonga.miuix.kmp.basic.ButtonDefaults.buttonColorsPrimary(),
                        ) {
                            Text("新建扫描")
                        }
                    }
                }
            }

            if (docs.isEmpty()) {
                item { EmptyHint("还没有扫描件", "保存后的 PDF 会出现在这里。") }
            } else {
                item { CardGap(16) }
                items(docs, key = { it.file.absolutePath }) { doc ->
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        onClick = { DocStore.open(doc) },
                    ) {
                        Row(
                            modifier = Modifier.padding(start = 16.dp, end = 8.dp, top = 10.dp, bottom = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(
                                    text = doc.name,
                                    fontSize = 15.sp,
                                    fontWeight = FontWeight.Medium,
                                    color = colorScheme.onSurface,
                                    maxLines = 1,
                                )
                                MutedText(
                                    "${formatSize(doc.size)} · ${formatDate(doc.savedAt)}",
                                    Modifier.padding(top = 3.dp),
                                )
                            }
                            IconButton(onClick = { DocStore.share(doc) }) {
                                Icon(
                                    MiuixIcons.Share,
                                    contentDescription = "分享",
                                    tint = colorScheme.onSurfaceVariantActions,
                                )
                            }
                            IconButton(onClick = { pendingDelete = doc }) {
                                Icon(
                                    MiuixIcons.Delete,
                                    contentDescription = "删除",
                                    tint = ToneRed,
                                )
                            }
                        }
                    }
                    CardGap(8)
                }
            }

            item { Spacer(Modifier.height(bottomPadding + 8.dp)) }
        }
    }

    pendingDelete?.let { doc ->
        OverlayDialog(
            show = true,
            title = "删除这个扫描件？",
            summary = doc.name,
            onDismissRequest = { pendingDelete = null },
            content = {
                Row(modifier = Modifier.fillMaxWidth()) {
                    TextButton(
                        text = "取消",
                        onClick = { pendingDelete = null },
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(
                        text = "删除",
                        onClick = {
                            DocStore.delete(doc)
                            docs = DocStore.list()
                            pendingDelete = null
                        },
                        modifier = Modifier.weight(1f),
                        colors = top.yukonga.miuix.kmp.basic.ButtonDefaults.textButtonColors(color = ToneRed),
                    )
                }
            },
        )
    }
}

private fun formatDate(ts: Long): String =
    SimpleDateFormat("M月d日 HH:mm", Locale.CHINA).format(Date(ts))
