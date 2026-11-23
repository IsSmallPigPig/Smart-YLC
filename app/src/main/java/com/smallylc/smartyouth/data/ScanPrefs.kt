package com.smallylc.smartyouth.data

import android.content.Context
import androidx.core.content.edit
import com.smallylc.smartyouth.app

/** 扫描默认参数：新建一页时用的滤镜、保存后是否立刻上传。 */
object ScanPrefs {

    private val prefs by lazy { app.getSharedPreferences("scan", Context.MODE_PRIVATE) }

    var defaultFilter: String
        get() = prefs.getString("filter", "ENHANCE").orEmpty()
        set(value) = prefs.edit { putString("filter", value) }

    var autoUpload: Boolean
        get() = prefs.getBoolean("auto_upload", false)
        set(value) = prefs.edit { putBoolean("auto_upload", value) }
}
