package com.smallylc.smartyouth.ui.navigation3

import android.os.Parcelable
import kotlinx.parcelize.Parcelize
import kotlinx.serialization.Serializable
import top.yukonga.miuix.kmp.nav.core.NavKey

/** 页面栈里的目的地。主界面是五个标签页，其余都是压栈的二级页。 */
@Serializable
sealed interface Route : NavKey, Parcelable {
    @Parcelize
    @Serializable
    data object Main : Route

    /** 登录 / 切换账号 */
    @Parcelize
    @Serializable
    data object Login : Route

    /** 拍照扫描：取景 → 多页 → 逐页调整 → 命名保存 */
    @Parcelize
    @Serializable
    data object Scan : Route

    /** 云端同步：用备份提取码取回支部账号 */
    @Parcelize
    @Serializable
    data object CloudSync : Route

    /** 传到电脑：扫码配对后只传电脑缺的档案 */
    @Parcelize
    @Serializable
    data object Pair : Route

    @Parcelize
    @Serializable
    data object About : Route

    @Parcelize
    @Serializable
    data object Theme : Route
}
