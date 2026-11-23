package com.smallylc.smartyouth

import android.app.Application
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import com.smallylc.smartyouth.site.SiteConstants
import okhttp3.Cache
import okhttp3.OkHttpClient
import org.opencv.android.OpenCVLoader
import java.io.File
import java.util.Locale
import java.util.concurrent.TimeUnit

lateinit var app: SmartYouthApplication

class SmartYouthApplication : Application(), ViewModelStoreOwner {

    lateinit var okhttpClient: OkHttpClient
        private set

    /** 纸边检测依赖 OpenCV 原生库，启动时加载一次；加载失败时扫描退化为手动拉角。 */
    var openCvReady: Boolean = false
        private set

    private val appViewModelStore by lazy { ViewModelStore() }

    override fun onCreate() {
        super.onCreate()
        app = this

        okhttpClient = OkHttpClient.Builder()
            .cache(Cache(File(cacheDir, "okhttp"), 10 * 1024 * 1024))
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .addInterceptor { chain ->
                chain.proceed(
                    chain.request().newBuilder()
                        .header("Accept-Language", Locale.getDefault().toLanguageTag())
                        .header("User-Agent", SiteConstants.USER_AGENT)
                        .build()
                )
            }
            .build()

        openCvReady = runCatching { OpenCVLoader.initLocal() }.getOrDefault(false)
    }

    override val viewModelStore: ViewModelStore
        get() = appViewModelStore
}
