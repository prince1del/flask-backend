package com.nexora.hop

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.animation.AccelerateInterpolator
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.ProgressBar
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private lateinit var splashOverlay: FrameLayout
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var splashHidden = false
    private val splashStartedAt = System.currentTimeMillis()
    private val mainHandler = Handler(Looper.getMainLooper())

    private val fileChooserLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val uris =
                WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
            filePathCallback?.onReceiveValue(uris)
            filePathCallback = null
        }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        progressBar = findViewById(R.id.progressBar)
        splashOverlay = findViewById(R.id.splashOverlay)

        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = false
            displayZoomControls = false
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = true
            allowContentAccess = true
            setSupportMultipleWindows(false)
            cacheMode = WebSettings.LOAD_DEFAULT
            javaScriptCanOpenWindowsAutomatically = false
        }

        webView.setLayerType(View.LAYER_TYPE_HARDWARE, null)

        webView.webViewClient =
            object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    request: WebResourceRequest,
                ): Boolean {
                    val url = request.url ?: return false
                    val host = url.host.orEmpty()
                    return if (host.endsWith("onrender.com") || host.contains("nexora")) {
                        false
                    } else {
                        startActivity(Intent(Intent.ACTION_VIEW, url))
                        true
                    }
                }

                override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                    progressBar.visibility = View.VISIBLE
                }

                override fun onPageFinished(view: WebView?, url: String?) {
                    progressBar.visibility = View.GONE
                    hideSplashWhenReady()
                }
            }

        webView.webChromeClient =
            object : WebChromeClient() {
                override fun onProgressChanged(view: WebView?, newProgress: Int) {
                    progressBar.progress = newProgress
                    progressBar.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
                    if (newProgress >= 90) hideSplashWhenReady()
                }

                override fun onShowFileChooser(
                    webView: WebView?,
                    filePathCallback: ValueCallback<Array<Uri>>?,
                    fileChooserParams: FileChooserParams?,
                ): Boolean {
                    this@MainActivity.filePathCallback?.onReceiveValue(null)
                    this@MainActivity.filePathCallback = filePathCallback
                    val intent =
                        fileChooserParams?.createIntent()
                            ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                                addCategory(Intent.CATEGORY_OPENABLE)
                                type = "*/*"
                            }
                    return try {
                        fileChooserLauncher.launch(intent)
                        true
                    } catch (_: Exception) {
                        this@MainActivity.filePathCallback = null
                        false
                    }
                }
            }

        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    handleAppBack()
                }
            },
        )

        if (savedInstanceState != null) {
            splashOverlay.visibility = View.GONE
            splashHidden = true
            webView.restoreState(savedInstanceState)
        } else {
            webView.loadUrl("${BuildConfig.START_URL}/?app=hop&v=${BuildConfig.VERSION_CODE}")
        }
    }

    private fun handleAppBack() {
        // 1) Close open menu drawer first (no exit)
        webView.evaluateJavascript(
            """
            (function(){
              try {
                if (document.body && document.body.classList.contains('mobile-nav-open')
                    && typeof closeMobileNav === 'function') {
                  closeMobileNav();
                  return 'drawer';
                }
              } catch (e) {}
              return 'none';
            })();
            """.trimIndent(),
        ) { raw ->
            val result = raw?.trim('"') ?: "none"
            if (result == "drawer") return@evaluateJavascript
            if (webView.canGoBack()) {
                webView.goBack()
            } else {
                confirmExitApp()
            }
        }
    }

    private fun confirmExitApp() {
        AlertDialog.Builder(this)
            .setTitle("Exit app?")
            .setMessage("Do you want to close House of Prizm?")
            .setPositiveButton("Exit") { _, _ -> finish() }
            .setNegativeButton("Cancel", null)
            .setCancelable(true)
            .show()
    }

    private fun hideSplashWhenReady() {
        if (splashHidden) return
        val elapsed = System.currentTimeMillis() - splashStartedAt
        val remaining = (1400L - elapsed).coerceAtLeast(0L)
        mainHandler.postDelayed({
            if (splashHidden || isFinishing) return@postDelayed
            splashHidden = true
            splashOverlay
                .animate()
                .alpha(0f)
                .setDuration(420)
                .setInterpolator(AccelerateInterpolator())
                .withEndAction {
                    splashOverlay.visibility = View.GONE
                    splashOverlay.alpha = 1f
                }
                .start()
        }, remaining)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }
}
