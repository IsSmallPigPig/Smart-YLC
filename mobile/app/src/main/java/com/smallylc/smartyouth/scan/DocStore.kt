package com.smallylc.smartyouth.scan

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import com.smallylc.smartyouth.app
import java.io.File

/** 本机扫描件库：文件放在应用外部目录，卸载应用才会清掉。 */
object DocStore {

    data class SavedDoc(
        val file: File,
        val name: String,
        val size: Long,
        val savedAt: Long,
    )

    val dir: File
        get() = File(app.getExternalFilesDir("documents"), "scan").apply { mkdirs() }

    fun list(): List<SavedDoc> =
        dir.listFiles()
            ?.filter { it.isFile && it.name.lowercase().endsWith(".pdf") }
            ?.sortedByDescending { it.lastModified() }
            ?.map { SavedDoc(it, it.name.removeSuffix(".pdf"), it.length(), it.lastModified()) }
            .orEmpty()

    /** 同名文件不覆盖：加 (2)(3) 后缀，名字仍可被站点解析。 */
    fun uniqueTarget(fileName: String): File {
        val base = fileName.removeSuffix(".pdf")
        var candidate = File(dir, "$base.pdf")
        var index = 2
        while (candidate.exists()) {
            candidate = File(dir, "$base($index).pdf")
            index++
        }
        return candidate
    }

    fun delete(doc: SavedDoc) {
        doc.file.delete()
    }

    fun share(doc: SavedDoc) {
        val uri = FileProvider.getUriForFile(app, "${app.packageName}.fileprovider", doc.file)
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "application/pdf"
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        app.startActivity(Intent.createChooser(intent, "分享扫描件").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    fun open(doc: SavedDoc) {
        val uri = FileProvider.getUriForFile(app, "${app.packageName}.fileprovider", doc.file)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/pdf")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        runCatching { app.startActivity(intent) }
    }
}
