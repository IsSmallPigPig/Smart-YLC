package com.smallylc.smartyouth.scan

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.smallylc.smartyouth.app
import java.io.File
import java.util.concurrent.Executor
import java.util.concurrent.Executors

/**
 * 取景与拍摄。相机实例跟着页面生命周期绑，离开扫描页就解绑，
 * 免得后面再进相机时抢不到设备。
 */
@Composable
fun CameraCapture(
    modifier: Modifier = Modifier,
    onReady: (CameraHandle) -> Unit = {},
    /** 逐帧回调，运行在分析线程；回调方负责回收传进来的 Bitmap。 */
    onFrame: ((android.graphics.Bitmap, Int) -> Unit)? = null,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var granted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED
        )
    }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it }
    val previewView = remember {
        PreviewView(context).apply {
            scaleType = PreviewView.ScaleType.FILL_CENTER
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        }
    }
    val executor: Executor = remember { ContextCompat.getMainExecutor(context) }
    val analysisExecutor = remember { Executors.newSingleThreadExecutor() }
    var imageCapture by remember { mutableStateOf<ImageCapture?>(null) }
    var provider by remember { mutableStateOf<ProcessCameraProvider?>(null) }
    var lensFacing by remember { mutableStateOf(CameraSelector.LENS_FACING_BACK) }

    LaunchedEffect(Unit) {
        if (!granted) launcher.launch(Manifest.permission.CAMERA)
    }

    DisposableEffect(Unit) {
        onDispose { analysisExecutor.shutdown() }
    }

    DisposableEffect(granted, lensFacing, lifecycleOwner) {
        if (!granted) return@DisposableEffect onDispose { }
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            val cameraProvider = runCatching { future.get() }.getOrNull() ?: return@addListener
            provider = cameraProvider
            val preview = Preview.Builder().build().also { it.surfaceProvider = previewView.surfaceProvider }
            val capture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                .build()
            imageCapture = capture
            val analysis = onFrame?.let { sink ->
                ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                    .build()
                    .also { useCase ->
                        useCase.setAnalyzer(analysisExecutor) { proxy ->
                            try {
                                sink(proxy.toBitmap(), proxy.imageInfo.rotationDegrees)
                            } catch (_: Throwable) {
                                // 单帧失败不影响取景
                            } finally {
                                proxy.close()
                            }
                        }
                    }
            }
            runCatching {
                cameraProvider.unbindAll()
                val cases = buildList {
                    add(preview)
                    add(capture)
                    analysis?.let { add(it) }
                }
                cameraProvider.bindToLifecycle(
                    lifecycleOwner,
                    CameraSelector.Builder().requireLensFacing(lensFacing).build(),
                    *cases.toTypedArray(),
                )
            }
        }, executor)
        onDispose {
            runCatching { provider?.unbindAll() }
        }
    }

    LaunchedEffect(imageCapture) {
        imageCapture?.let { capture ->
            onReady(
                CameraHandle(
                    capture = capture,
                    toggleLens = {
                        lensFacing = if (lensFacing == CameraSelector.LENS_FACING_BACK) {
                            CameraSelector.LENS_FACING_FRONT
                        } else {
                            CameraSelector.LENS_FACING_BACK
                        }
                    },
                )
            )
        }
    }

    Box(modifier.fillMaxSize()) {
        if (granted) {
            AndroidView(factory = { previewView }, modifier = Modifier.fillMaxSize())
        }
    }
}

class CameraHandle(
    private val capture: ImageCapture,
    val toggleLens: () -> Unit,
) {
    fun shoot(onSaved: (File) -> Unit, onError: (String) -> Unit) {
        val dir = File(app.cacheDir, "scan/incoming").apply { mkdirs() }
        val file = File(dir, "shot_${System.currentTimeMillis()}.jpg")
        val options = ImageCapture.OutputFileOptions.Builder(file).build()
        capture.takePicture(
            options,
            ContextCompat.getMainExecutor(app),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                    onSaved(file)
                }

                override fun onError(exception: ImageCaptureException) {
                    onError(exception.message ?: "拍摄失败")
                }
            },
        )
    }
}
