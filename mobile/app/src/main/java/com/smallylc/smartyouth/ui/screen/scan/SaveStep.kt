package com.smallylc.smartyouth.ui.screen.scan

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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.smallylc.smartyouth.app
import com.smallylc.smartyouth.data.Repo
import com.smallylc.smartyouth.scan.DocStore
import com.smallylc.smartyouth.scan.Naming
import com.smallylc.smartyouth.scan.ScanPage
import com.smallylc.smartyouth.scan.ScanSession
import com.smallylc.smartyouth.site.Member
import com.smallylc.smartyouth.ui.component.CardGap
import com.smallylc.smartyouth.ui.component.MutedText
import com.smallylc.smartyouth.ui.component.ValueRow
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
import top.yukonga.miuix.kmp.basic.LinearProgressIndicator
import top.yukonga.miuix.kmp.basic.MiuixScrollBehavior
import top.yukonga.miuix.kmp.basic.Scaffold
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.basic.TextField
import top.yukonga.miuix.kmp.basic.TextButton
import top.yukonga.miuix.kmp.basic.TopAppBar
import top.yukonga.miuix.kmp.basic.ButtonDefaults
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Back
import top.yukonga.miuix.kmp.theme.MiuixTheme.colorScheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import top.yukonga.miuix.kmp.utils.scrollEndHaptic
import java.io.File

/**
 * 保存页：文件名 + 团员模糊搜索。
 *
 * 名字是站点匹配团员的唯一凭据，所以这里把「搜到谁」直接摆出来 ——
 * 点一条就把 姓名+手机号+入团志愿书 补齐，不用用户背格式。
 */
@Composable
internal fun SaveStep(
    pages: List<ScanPage>,
    prefillName: String,
    onBack: () -> Unit,
    onExit: () -> Unit,
) {
    var name by rememberSaveable { mutableStateOf(prefillName) }
    var query by rememberSaveable { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    var saved by remember { mutableStateOf<DocStore.SavedDoc?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var uploading by remember { mutableStateOf(false) }
    var uploadProgress by remember { mutableStateOf(0f) }
    var uploadResult by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val scrollBehavior = MiuixScrollBehavior()
    val backdrop = rememberBlurBackdrop(LocalEnableBlur.current)
    val barColor = if (backdrop != null) androidx.compose.ui.graphics.Color.Transparent else colorScheme.surface

    val suggestions = remember(query) {
        val text = query.trim().removeSuffix(".pdf")
        if (text.isEmpty()) emptyList() else Repo.searchMembers(text, limit = 6)
    }
    val check = Naming.check(name.ifBlank { " " })

    Scaffold(
        topBar = {
            BlurredBar(backdrop) {
                TopAppBar(
                    title = "保存扫描件",
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
                .nestedScroll(scrollBehavior.nestedScrollConnection)
                .padding(horizontal = 26.dp),
            contentPadding = innerPadding,
            overscrollEffect = null,
        ) {
            item { Spacer(Modifier.height(8.dp)) }

            if (saved == null) {
                // 搜索框与文件名分开：输入姓名或手机号 → 点结果 → 文件名自动补齐。
                item {
                    Card(Modifier.fillMaxWidth()) {
                        TextField(
                            value = query,
                            onValueChange = { query = it },
                            label = "搜索团员姓名或手机号",
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true,
                        )
                        if (suggestions.isNotEmpty()) {
                            Column(Modifier.padding(horizontal = 8.dp, vertical = 4.dp)) {
                                suggestions.forEach { member ->
                                    SuggestionRow(member) {
                                        name = Naming.fileNameFor(member)
                                        query = ""
                                        error = null
                                    }
                                }
                            }
                        } else if (query.isNotBlank()) {
                            MutedText(
                                text = "名单里没有这个人，也可以直接在下面手写文件名。",
                                modifier = Modifier.padding(start = 16.dp, bottom = 12.dp),
                            )
                        }
                    }
                }

                item {
                    CardGap(8)
                    Card(Modifier.fillMaxWidth()) {
                        TextField(
                            value = name,
                            onValueChange = {
                                name = it
                                error = null
                                uploadResult = null
                            },
                            label = "文件名",
                            modifier = Modifier.fillMaxWidth(),
                            singleLine = true,
                        )
                        Column(Modifier.padding(start = 16.dp, end = 16.dp, bottom = 12.dp)) {
                            MutedText(
                                text = if (check.ok) "站点按这个文件名匹配团员" else check.hint,
                                modifier = Modifier.padding(top = 4.dp),
                                color = if (check.ok) ToneGreen else ToneRed,
                            )
                        }
                    }
                }

                item {
                    CardGap()
                    Card(Modifier.fillMaxWidth()) {
                        ValueRow("页数", "${pages.size} 页")
                        ValueRow("保存位置", "本机扫描件")
                    }
                }

                if (error != null) {
                    item {
                        Text(
                            text = error!!,
                            color = ToneRed,
                            fontSize = 13.sp,
                            modifier = Modifier.padding(start = 8.dp, top = 8.dp),
                        )
                    }
                }

                item {
                    Button(
                        onClick = {
                            if (!check.ok || pages.isEmpty()) return@Button
                            saving = true
                            error = null
                            scope.launch {
                                val target = DocStore.uniqueTarget("${name.trim().removeSuffix(".pdf")}.pdf")
                                val temp = File(app.cacheDir, "scan/export.pdf")
                                val result = ScanSession.buildPdf(temp)
                                if (result == null) {
                                    saving = false
                                    error = "页面还在准备，稍后再试"
                                    return@launch
                                }
                                withContext(Dispatchers.IO) {
                                    target.parentFile?.mkdirs()
                                    temp.copyTo(target, overwrite = true)
                                    temp.delete()
                                }
                                saved = DocStore.SavedDoc(
                                    file = target,
                                    name = target.name.removeSuffix(".pdf"),
                                    size = target.length(),
                                    savedAt = System.currentTimeMillis(),
                                )
                                saving = false
                            }
                        },
                        enabled = check.ok && !saving && pages.isNotEmpty(),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 16.dp),
                        colors = ButtonDefaults.buttonColorsPrimary(),
                    ) {
                        Text(if (saving) "正在生成…" else "保存档案")
                    }
                }
                item { Spacer(Modifier.height(24.dp)) }
            } else {
                val doc = saved!!
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    text = "已保存",
                                    fontSize = 16.sp,
                                    fontWeight = FontWeight.Medium,
                                    color = ToneGreen,
                                )
                            }
                            Text(
                                text = doc.file.name,
                                fontSize = 14.sp,
                                color = colorScheme.onSurface,
                                modifier = Modifier.padding(top = 6.dp),
                            )
                            MutedText(
                                text = "${pages.size} 页  ${formatSize(doc.size)}",
                                modifier = Modifier.padding(top = 4.dp),
                            )
                        }
                    }
                }

                if (uploadResult != null) {
                    item {
                        CardGap(8)
                        Card(Modifier.fillMaxWidth()) {
                            Text(
                                text = uploadResult!!,
                                fontSize = 14.sp,
                                color = if (uploadResult!!.startsWith("上传成功")) ToneGreen else ToneRed,
                                modifier = Modifier.padding(16.dp),
                            )
                        }
                    }
                }

                if (uploading) {
                    item {
                        CardGap(8)
                        Card(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(16.dp)) {
                                Text("正在上传到站点…", fontSize = 14.sp, color = colorScheme.onSurface)
                                LinearProgressIndicator(
                                    progress = uploadProgress,
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(top = 10.dp),
                                )
                            }
                        }
                    }
                }

                item {
                    CardGap()
                    Card(Modifier.fillMaxWidth()) {
                        TextButton(
                            text = "上传档案",
                            onClick = {
                                uploading = true
                                uploadResult = null
                                scope.launch {
                                    val outcome = uploadDocument(doc, name) { done, total ->
                                        uploadProgress = if (total == 0) 0f else done.toFloat() / total
                                    }
                                    uploading = false
                                    uploadResult = outcome
                                }
                            },
                            enabled = !uploading,
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.textButtonColorsPrimary(),
                        )
                        TextButton(
                            text = "完成扫描",
                            onClick = {
                                ScanSession.clear()
                                onExit()
                            },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
                item { Spacer(Modifier.height(24.dp)) }
            }
        }
    }
}

@Composable
private fun SuggestionRow(member: Member, onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp, vertical = 2.dp),
        cornerRadius = 8.dp,
        onClick = onClick,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    text = member.name,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Medium,
                    color = colorScheme.onSurface,
                )
                MutedText(
                    listOfNotNull(
                        member.mobile.takeIf { it.isNotBlank() },
                        member.displayOrg.takeIf { it.isNotBlank() },
                    ).joinToString("  "),
                    Modifier.padding(top = 2.dp),
                )
            }
            if (member.uploaded) {
                Text("已上传", fontSize = 12.sp, color = ToneGreen)
            }
        }
    }
}

internal fun formatSize(bytes: Long): String = when {
    bytes >= 1024 * 1024 -> String.format("%.1f MB", bytes / 1024f / 1024f)
    bytes >= 1024 -> String.format("%.0f KB", bytes / 1024f)
    else -> "$bytes B"
}

/** 上传：先找到对应团员的 mid，再走分片上传 + 自动审核。 */
private suspend fun uploadDocument(
    doc: DocStore.SavedDoc,
    fileName: String,
    onProgress: (Int, Int) -> Unit,
): String = withContext(Dispatchers.IO) {
    val client = Repo.clientFor() ?: return@withContext "还没登录，先到「我的」登录账号"
    val baseName = fileName.trim().removeSuffix(".pdf")
    val parts = baseName.split("+")
    val member = if (parts.size == 3) {
        Repo.members.value.firstOrNull { it.name == parts[0] && it.mobile == parts[1] }
    } else null
    runCatching {
        val (mid, _) = client.uploadPdf(doc.file, doc.file.name, onProgress)
        val auditMid = mid.ifBlank { member?.mid.orEmpty() }
        if (auditMid.isNotBlank()) {
            val (ok, message) = client.auditVoluntaryBook(auditMid)
            if (ok) "上传成功，已提交审核" else "上传成功，但审核没通过：$message"
        } else {
            "上传成功，但没在名单里找到对应团员，请到电脑端确认审核状态"
        }
    }.getOrElse { "上传失败：${it.message}" }
}
