package com.smallylc.smartyouth.ui.component

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.smallylc.smartyouth.update.UpdateManager
import com.smallylc.smartyouth.update.UpdateUi
import top.yukonga.miuix.kmp.basic.TextButton
import top.yukonga.miuix.kmp.overlay.OverlayDialog

/** 更新状态对应的行摘要。文案只报状态，不教用户怎么操作。 */
fun updateSummary(state: UpdateUi): String = when (state) {
    UpdateUi.Checking -> "正在检查…"
    is UpdateUi.Available -> "新版本 v${state.info.version}${sizeText(state.info.size)}"
    is UpdateUi.Downloading -> "下载中 ${state.percent}%"
    is UpdateUi.Ready -> "新版本已下载"
    is UpdateUi.Failed -> state.reason
    UpdateUi.Latest -> "已是最新"
    UpdateUi.Idle -> "检查 GitHub 上的手机版发布"
}

/** 检查更新行的点击动作。Ready 时点行再次拉起安装，属于用户主动。 */
fun updateRowClick(state: UpdateUi): () -> Unit = when (state) {
    is UpdateUi.Ready -> {
        val uri = state.uri
        { UpdateManager.installReady(uri) }
    }
    is UpdateUi.Available, is UpdateUi.Downloading, UpdateUi.Checking -> ({ })
    else -> ({ UpdateManager.check() })
}

/**
 * 挂在 MainScreen 的更新对话框：从「发现新版本」到「下载完成」都留在同一张卡片上，
 * 进度实时刷新，下载结束后才出现「安装」。自动检查与手动检查共用一份状态。
 */
@Composable
fun UpdateDialogHost() {
    val state by UpdateManager.state.collectAsStateWithLifecycle()
    val current = state
    // 「后台」只把对话框收起，下载照样跑；离开下载态（Ready/Failed）时再弹出来。
    var backgrounded by remember { mutableStateOf(false) }
    LaunchedEffect(current is UpdateUi.Downloading) {
        if (current !is UpdateUi.Downloading) backgrounded = false
    }
    val info = when (val s = current) {
        is UpdateUi.Available -> s.info
        is UpdateUi.Downloading -> s.info
        is UpdateUi.Ready -> s.info
        else -> return
    }
    if (backgrounded && current is UpdateUi.Downloading) return

    val summary = when (val s = current) {
        is UpdateUi.Downloading -> "下载中 ${s.percent}%"
        is UpdateUi.Ready -> "已下载${sizeText(info.size)}，可以安装"
        else -> "当前 v${UpdateManager.currentVersion}${sizeText(info.size)}"
    }
    val ready = current is UpdateUi.Ready
    val downloaded = current is UpdateUi.Downloading
    OverlayDialog(
        show = true,
        title = "发现新版本 v${info.version}",
        summary = summary,
        onDismissRequest = { UpdateManager.dismiss() },
        content = {
            Row(modifier = Modifier.fillMaxWidth()) {
                TextButton(
                    text = when {
                        ready -> "稍后"
                        downloaded -> "后台"
                        else -> "忽略"
                    },
                    onClick = {
                        when (current) {
                            is UpdateUi.Ready -> UpdateManager.dismiss()
                            is UpdateUi.Downloading -> backgrounded = true
                            else -> UpdateManager.ignore(info.version)
                        }
                    },
                    modifier = Modifier.weight(1f),
                )
                TextButton(
                    text = if (ready) "安装" else "更新",
                    onClick = {
                        when (val s = current) {
                            is UpdateUi.Downloading -> Unit
                            is UpdateUi.Ready -> UpdateManager.installReady(s.uri)
                            else -> UpdateManager.download(info)
                        }
                    },
                    modifier = Modifier.weight(1f),
                )
            }
        },
    )
}

private fun sizeText(size: Long): String =
    if (size > 0) "，${com.smallylc.smartyouth.ui.screen.scan.formatSize(size)}" else ""
