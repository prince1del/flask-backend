# House of Prizm — Android WebView app

Wraps the live NEXORA / HoP site in a simple Android shell.

## Default URL

`https://flask-backend-wnlq.onrender.com`

Change in `app/build.gradle.kts` → `START_URL`.

## Build APK (Windows)

1. Install / open **Android Studio** once (SDK already used if present).
2. In PowerShell:

```powershell
cd android
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat assembleDebug
```

3. APK path:

`android/app/build/outputs/apk/debug/app-debug.apk`

## Install on phone

1. Copy `app-debug.apk` to phone
2. Enable **Install unknown apps** for Files/Chrome
3. Open APK → Install
4. Login: `hop_prizm` / `Prizm@2026!`

## Android Studio

**File → Open** → select the `android` folder → Run on device/emulator.
