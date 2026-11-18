package com.smallylc.smartyouth

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.io.File
import top.yukonga.miuix.kmp.basic.BasicComponent
import top.yukonga.miuix.kmp.basic.Button
import top.yukonga.miuix.kmp.basic.Card
import top.yukonga.miuix.kmp.basic.HorizontalDivider
import top.yukonga.miuix.kmp.basic.Icon
import top.yukonga.miuix.kmp.basic.IconButton
import top.yukonga.miuix.kmp.basic.Text
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.extended.Delete
import top.yukonga.miuix.kmp.icon.extended.Photos
import top.yukonga.miuix.kmp.theme.MiuixTheme

private fun archiveDir(ctx: Context): File =
    File(ctx.filesDir, "archive").apply { mkdirs() }

private fun displayName(ctx: Context, uri: Uri): String {
    ctx.contentResolver.query(uri, null, null, null, null)?.use { c ->
        val i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        if (i >= 0 && c.moveToFirst()) return c.getString(i) ?: "photo.jpg"
    }
    return "photo.jpg"
}

private fun importPhoto(ctx: Context, uri: Uri): File? = runCatching {
    val name = displayName(ctx, uri)
    val target = File(archiveDir(ctx), "${System.currentTimeMillis()}-$name")
    ctx.contentResolver.openInputStream(uri)?.use { input ->
        target.outputStream().use { input.copyTo(it) }
    }
    target
}.getOrNull()

private fun humanSize(bytes: Long): String =
    if (bytes > 1024 * 1024) "%.1f MB".format(bytes / 1024f / 1024f) else "${bytes / 1024} KB"

@Composable
fun FilesScreen() {
    val ctx = LocalContext.current
    var files by remember { mutableStateOf(archiveDir(ctx).listFiles()?.sortedByDescending { it.lastModified() } ?: emptyList()) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        uris.forEach { importPhoto(ctx, it) }
        files = archiveDir(ctx).listFiles()?.sortedByDescending { it.lastModified() } ?: emptyList()
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("入团志愿书", style = MiuixTheme.textStyles.main.copy(fontSize = 17.sp, fontWeight = FontWeight.SemiBold))
                Text("${files.size} 页 · 存于应用内", style = MiuixTheme.textStyles.main.copy(fontSize = 12.sp))
                Button(onClick = { picker.launch("image/*") }, modifier = Modifier.fillMaxWidth()) {
                    Icon(MiuixIcons.Photos, contentDescription = null)
                    Spacer(Modifier.width(6.dp))
                    Text("拍照 / 选择图片")
                }
            }
        }

        if (files.isEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text("还没有页面", style = MiuixTheme.textStyles.main.copy(fontSize = 13.sp))
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                items(files, key = { it.absolutePath }) { file ->
                    Card(Modifier.fillMaxWidth().padding(bottom = 8.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            BasicComponent(
                                title = file.name.substringAfter('-'),
                                summary = humanSize(file.length()),
                                modifier = Modifier.weight(1f),
                            )
                            IconButton(onClick = {
                                file.delete()
                                files = archiveDir(ctx).listFiles()?.sortedByDescending { it.lastModified() } ?: emptyList()
                            }) { Icon(MiuixIcons.Delete, contentDescription = "删除") }
                        }
                    }
                }
            }
        }
    }
}
