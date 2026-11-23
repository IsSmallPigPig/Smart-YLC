package com.smallylc.smartyouth.ui.viewmodel

import androidx.compose.runtime.Immutable
import com.smallylc.smartyouth.ui.theme.AppSettings

@Immutable
data class MainActivityUiState(
    val appSettings: AppSettings,
    val pageScale: Float,
    val enableBlur: Boolean,
)
