# Keep WebView bridge symbols if added later.
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
