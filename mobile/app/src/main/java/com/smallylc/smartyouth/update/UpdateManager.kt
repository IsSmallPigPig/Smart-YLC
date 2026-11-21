package com.smallylc.smartyouth.update

import android.content.Context
import android.net.Uri
import androidx.core.content.edit
import com.smallylc.smartyouth.BuildConfig
import com.smallylc.smartyouth.app
import com.smallylc.smartyouth.data.repository.SettingsRepository
import com.smallylc.smartyouth.data.repository.SettingsRepositoryImpl
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** 更新流程的界面状态。进程级单例，自动检查与手动检查共用一份结果。 */
sealed interface UpdateUi {
    data object Idle : UpdateUi
    data object Checking : UpdateUi
    data class Available(val info: GitHubUpdate.Info) : UpdateUi
    data class Downloading(val info: GitHubUpdate.Info, val percent: Int) : UpdateUi
    data class Ready(val info: GitHubUpdate.Info, val uri: Uri) : UpdateUi
    data class Failed(val reason: String) : UpdateUi
    data object Latest : UpdateUi
}

object UpdateManager {

    // 自动检查开关与 SettingsRepository.checkUpdate 同源，不另建一套 prefs。
    private val settings: SettingsRepository = SettingsRepositoryImpl()
    private val updatePrefs by lazy { app.getSharedPreferences("update", Context.MODE_PRIVATE) }

    var autoCheck: Boolean
        get() = settings.checkUpdate
        set(value) { settings.checkUpdate = value }

    /** 用户点过「忽略」的版本，自动检查不再打扰；手动检查仍会提示。 */
    var ignoredVersion: String
        get() = updatePrefs.getString("ignored", "").orEmpty()
        set(value) = updatePrefs.edit { putString("ignored", value) }

    /** 冷启动检查节流：刚查过就别再打 GitHub API。 */
    private var lastCheckMs: Long
        get() = updatePrefs.getLong("last_check", 0L)
        set(value) = updatePrefs.edit { putLong("last_check", value) }

    private val _state = MutableStateFlow<UpdateUi>(UpdateUi.Idle)
    val state: StateFlow<UpdateUi> = _state.asStateFlow()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    /** 启动后的静默检查：失败不打扰用户，也不改变界面。 */
    fun autoCheckNow() {
        if (!autoCheck || _state.value != UpdateUi.Idle) return
        if (System.currentTimeMillis() - lastCheckMs < 6 * 60 * 60 * 1000L) return
        check(showFailure = false)
    }

    fun check(showFailure: Boolean = true) {
        // 正在检查/下载、或已下载待安装时，别让新结果把入口冲掉。
        if (_state.value is UpdateUi.Checking ||
            _state.value is UpdateUi.Downloading ||
            _state.value is UpdateUi.Ready
        ) return
        lastCheckMs = System.currentTimeMillis()
        scope.launch {
            _state.value = UpdateUi.Checking
            when (val result = GitHubUpdate.check()) {
                is GitHubUpdate.CheckResult.NewVersion -> {
                    val info = result.info
                    val muted = !showFailure && info.version == ignoredVersion
                    _state.value = if (muted) UpdateUi.Idle else UpdateUi.Available(info)
                }
                GitHubUpdate.CheckResult.UpToDate ->
                    _state.value = if (showFailure) UpdateUi.Latest else UpdateUi.Idle
                is GitHubUpdate.CheckResult.Failed ->
                    _state.value = if (showFailure) UpdateUi.Failed(result.reason) else UpdateUi.Idle
            }
        }
    }

    /**
     * 下载完成后只停在 Ready，等用户点「安装」——
     * 和桌面端同一条铁律：下载绝不自动拉起安装。
     */
    fun download(info: GitHubUpdate.Info) {
        if (_state.value is UpdateUi.Downloading) return
        scope.launch {
            _state.value = UpdateUi.Downloading(info, 0)
            GitHubUpdate.download(info) { percent ->
                _state.value = UpdateUi.Downloading(info, percent)
            }.onSuccess { uri ->
                _state.value = UpdateUi.Ready(info, uri)
            }.onFailure {
                _state.value = UpdateUi.Failed(it.message ?: "下载失败")
            }
        }
    }

    /** 用户点「安装」才跳转：未授权时先去「安装未知应用」设置页。 */
    fun installReady(uri: Uri) {
        if (!GitHubUpdate.canInstall()) {
            app.startActivity(
                android.content.Intent(
                    android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:${app.packageName}"),
                ).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
            )
        } else {
            app.startActivity(GitHubUpdate.installIntent(uri))
        }
    }

    fun ignore(version: String) {
        ignoredVersion = version
        _state.value = UpdateUi.Idle
    }

    fun dismiss() {
        _state.value = UpdateUi.Idle
    }

    val currentVersion: String get() = BuildConfig.VERSION_NAME
}
