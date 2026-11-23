package com.smallylc.smartyouth.ui.viewmodel

import android.content.Context
import android.content.SharedPreferences
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import com.smallylc.smartyouth.app
import com.smallylc.smartyouth.data.repository.SettingsRepository
import com.smallylc.smartyouth.data.repository.SettingsRepositoryImpl
import com.smallylc.smartyouth.ui.theme.ThemeController
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class MainActivityViewModel(
    savedStateHandle: SavedStateHandle,
) : ViewModel() {

    private val prefs = app.getSharedPreferences("settings", Context.MODE_PRIVATE)
    private val settingRepo: SettingsRepository = SettingsRepositoryImpl()
    private val mainPageState = MainPageState(savedStateHandle)
    private val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key == null || key in observedKeys) _uiState.value = readUiState()
    }

    private val _uiState = MutableStateFlow(readUiState())
    val uiState: StateFlow<MainActivityUiState> = _uiState.asStateFlow()
    val selectedMainPage: StateFlow<Int> = mainPageState.selectedPage

    init {
        prefs.registerOnSharedPreferenceChangeListener(listener)
    }

    override fun onCleared() {
        prefs.unregisterOnSharedPreferenceChangeListener(listener)
    }

    fun setSelectedMainPage(page: Int) {
        mainPageState.updateSelectedPage(page)
    }

    private fun readUiState() = MainActivityUiState(
        appSettings = ThemeController.getAppSettings(app),
        pageScale = settingRepo.pageScale,
        enableBlur = settingRepo.enableBlur,
    )

    private companion object {
        val observedKeys = setOf(
            "color_mode", "key_color", "color_style", "color_spec",
            "page_scale", "enable_blur", "miuix_monet",
        )
    }
}

private const val SELECTED_MAIN_PAGE_KEY = "selected_main_page"

private class MainPageState(private val savedStateHandle: SavedStateHandle) {
    init {
        val savedPage = savedStateHandle.get<Int>(SELECTED_MAIN_PAGE_KEY) ?: 0
        savedStateHandle[SELECTED_MAIN_PAGE_KEY] = MainPagerConfig.coercePage(savedPage)
    }

    val selectedPage: StateFlow<Int> = savedStateHandle.getStateFlow(SELECTED_MAIN_PAGE_KEY, 0)

    fun updateSelectedPage(page: Int) {
        savedStateHandle[SELECTED_MAIN_PAGE_KEY] = MainPagerConfig.coercePage(page)
    }
}

object MainPagerConfig {
    const val PAGE_COUNT = 5
    const val LAST_PAGE_INDEX = PAGE_COUNT - 1

    fun coercePage(page: Int): Int = page.coerceIn(0, LAST_PAGE_INDEX)
}
