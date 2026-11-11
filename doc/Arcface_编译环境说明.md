# Arcface App 编译环境说明

> **文档版本**：v1.0（2026-09-08）
> **项目**：TVPilot 子项目 — Arcface App（电视端人脸识别 + 无障碍服务 + HTTP 服务）
> **包名**：`com.example.arcfaceandroid`

---

## 一、项目概述

Arcface App 运行在电视端，提供以下能力：
- 人脸识别（ArcFace SDK，Camera2 方案）
- 人脸注册管理（HTTP Web 界面，:8080）
- 无障碍服务 `TvAccessibilityService`（控件级操作，v0.4 扩展基础）
- 电视打断链路自愈（小爱同学关键词监听）
- 开机自启 + 看门狗保活

**源码位置**：`D:\Documents\WorkSpace\TV_CAM\Arcface\`
**APK 输出**：`app/build/outputs/apk/debug/app-debug.apk`（约 93MB）

---

## 二、编译环境要求

| 组件 | 版本要求 | 当前状态 | 路径 |
|------|---------|---------|------|
| JDK | **17**（AGP 8.1.4 官方要求） | ✅ 已安装（2026-09-08 解压） | `D:\jdk17\jdk-17.0.20.1+1` |
| Gradle | 8.4 | ✅ 已缓存 | `~/.gradle/wrapper/dists/gradle-8.4-bin/.../gradle-8.4/bin/gradle.bat` |
| Android Gradle Plugin | 8.1.4 | ✅ 已缓存依赖 | `~/.gradle/caches/` |
| Android SDK | compileSdk 34 | ✅ 已安装 | `C:\Users\lidicn\AppData\Local\Android\Sdk` |
| Android Studio | 任意版本 | ✅ 已安装 | `C:\Program Files\Android\Android Studio` |
| Android Studio 自带 JDK (JBR) | 21.0.10 | ⚠️ 存在但不兼容 AGP 8.1.4 | `C:\Program Files\Android\Android Studio\jbr` |

> **JDK 17 来源**：`D:\jdk17.zip`（182MB，Eclipse Temurin 17.0.20.1+1），解压到 `D:\jdk17\jdk-17.0.20.1+1\`。

### 项目配置（app/build.gradle）

```groovy
namespace 'com.example.arcfaceandroid'
applicationId "com.example.arcfaceandroid"
compileSdk 34
minSdk 24
targetSdk 34
```

### 根 build.gradle

```groovy
classpath 'com.android.tools.build:gradle:8.1.4'
// 镜像源：腾讯云 maven + google_maven2
```

### gradle-wrapper.properties

```properties
distributionUrl=https://mirrors.cloud.tencent.com/gradle/gradle-8.4-bin.zip
```

### local.properties

```properties
sdk.dir=C:\\Users\\lidicn\\AppData\\Local\\Android\\Sdk
```

---

## 三、当前问题：JDK 21 与 AGP 8.1.4 不兼容

### 现象

执行 `gradle assembleDebug` 时，`compileDebugJavaWithJavac` 任务失败：

```
FAILURE: Build failed with an exception.
* What went wrong:
Execution failed for task ':app:compileDebugJavaWithJavac'.
> Could not resolve all files for configuration ':app:androidJdkImage'.
   > Failed to transform core-for-system-modules.jar ...
      > Execution failed for JdkImageTransform: ...\platforms\android-34\core-for-system-modules.jar
         > Error while executing process ...\jbr\bin\jlink.exe with arguments {...}
```

### 原因

- AGP 8.1.4 官方支持 JDK 17，**不支持 JDK 21**
- Android Studio 自带 JBR 是 JDK 21.0.10
- JDK 21 的 `jlink` 工具与 AGP 8.1.4 的 `JdkImageTransform` 不兼容

### 解决方案（三选一）

#### 方案 A：安装 JDK 17（✅ 已完成，推荐，最稳定）

JDK 17 已解压到 `D:\jdk17\jdk-17.0.20.1+1\`（来源 `D:\jdk17.zip`，Eclipse Temurin 17.0.20.1+1）。

构建时设置环境变量：
```powershell
$env:JAVA_HOME = "D:\jdk17\jdk-17.0.20.1+1"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
```
执行构建：
```powershell
cd D:\Documents\WorkSpace\TV_CAM\Arcface
& "C:\Users\lidicn\.gradle\wrapper\dists\gradle-8.4-bin\cnqr4qbc93ir6sp626e40kx2s\gradle-8.4\bin\gradle.bat" assembleDebug
```

#### 方案 B：升级 AGP 到 8.3+（支持 JDK 21）

修改根 `build.gradle`：
```groovy
classpath 'com.android.tools.build:gradle:8.3.2'  // 或更高
```
- 优点：继续用 JDK 21，无需安装新 JDK
- 风险：可能需要同步升级依赖版本，ArcFace SDK 兼容性需验证

#### 方案 C：用 Android Studio IDE 构建

- Android Studio 可能自动选择兼容的 JDK（或内置 JDK 兼容性处理）
- 打开项目 → Build → Make Project
- 但**命令行构建仍然需要正确的 JDK**，IDE 构建不适合自动化部署

---

## 四、缺失文件补充

项目根目录缺少以下 Gradle Wrapper 文件（不影响用缓存 gradle 构建，但影响 `gradlew` 命令）：

| 文件 | 状态 | 修复方式 |
|------|------|---------|
| `gradlew.bat` | ❌ 缺失 | 从 `samplecode/ArcfaceDemo/gradlew.bat` 复制 |
| `gradle/wrapper/gradle-wrapper.jar` | ❌ 缺失 | 从 `samplecode/ArcfaceDemo/gradle/wrapper/gradle-wrapper.jar` 复制 |

**一键修复**：
```powershell
Copy-Item "D:\Documents\WorkSpace\TV_CAM\Arcface\samplecode\ArcfaceDemo\gradlew.bat" "D:\Documents\WorkSpace\TV_CAM\Arcface\gradlew.bat"
Copy-Item "D:\Documents\WorkSpace\TV_CAM\Arcface\samplecode\ArcfaceDemo\gradle\wrapper\gradle-wrapper.jar" "D:\Documents\WorkSpace\TV_CAM\Arcface\gradle\wrapper\gradle-wrapper.jar"
```

修复后可用 `.\gradlew.bat assembleDebug` 构建。

---

## 五、构建与部署步骤

### 5.1 构建 Debug APK

```powershell
# 1. 设置 JDK 17（如已安装）
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.x.x-hotspot"

# 2. 构建
cd D:\Documents\WorkSpace\TV_CAM\Arcface
& "C:\Users\lidicn\.gradle\wrapper\dists\gradle-8.4-bin\cnqr4qbc93ir6sp626e40kx2s\gradle-8.4\bin\gradle.bat" assembleDebug

# 3. 输出位置
# app\build\outputs\apk\debug\app-debug.apk
```

### 5.2 安装到电视

```powershell
$adb = "C:\Users\lidicn\AppData\Local\Android\Sdk\platform-tools\adb.exe"
& $adb connect 192.168.2.238:5555
& $adb -s 192.168.2.238:5555 install -r app\build\outputs\apk\debug\app-debug.apk
```

### 5.3 验证

```powershell
# 检查服务是否运行
& $adb -s 192.168.2.238:5555 shell "pidof com.example.arcfaceandroid"

# 检查 HTTP 服务
Invoke-WebRequest "http://192.168.2.238:8080/api/health" -UseBasicParsing
```

---

## 六、电视端相关应用包名对照表

> **重要**：以下包名已在电视上验证，开发时请勿使用错误名称。

| 应用 | 包名 | 入口 Activity | 说明 |
|------|------|--------------|------|
| **飞牛TV** | `com.trim.tv` | `.modules.splash.SplashActivity` | ⚠️ 前任开发者手误写成 `fongmi`，实际是 `trim` |
| Arcface App | `com.example.arcfaceandroid` | `.TvMainActivity` | 人脸识别 + 无障碍 + HTTP |
| mytv 个性化版 | `com.tvcam.mytv` | `.activities.LeanbackActivity` | IPTV 播放器 |
| tvremoteime | `com.android.tvremoteime` | `.IMEService` | ADB 远程输入法（中文输入用） |
| 小米桌面 | `com.mitv.tvhome` | `.MainActivityUserMode` | 电视桌面 |

### 飞牛TV 详情

- 版本：1.3.4
- 播放器 Activity：`.modules.media.player.PlayerActivity`
- 用途：TVPilot v0.3 `combo/trim_search_play` 组合动作的目标应用
- 历史命名问题：旧代码中 `FONGMI_TAP`、`fongmi_search`、`MQTT_IN_FONGMI` 等名称均为手误，应逐步修正为 `trim`

---

## 七、项目结构

```
Arcface/
├── app/
│   ├── src/main/java/com/example/arcfaceandroid/
│   │   ├── TvMainActivity.java          # 主界面
│   │   ├── FaceServerService.java       # 前台服务（人脸识别+HTTP）
│   │   ├── TvAccessibilityService.java  # 无障碍服务（v0.4 扩展基础）
│   │   ├── ClipboardReceiver.java       # 剪贴板广播接收器（备选中文输入方案，v0.3 添加）
│   │   ├── ServiceLauncherReceiver.java # 服务拉起广播
│   │   ├── BootReceiver.java            # 开机自启
│   │   ├── AlarmWatchdogReceiver.java   # 看门狗
│   │   └── RegisterActivity.java        # 人脸注册引导
│   ├── build.gradle
│   └── build/outputs/apk/debug/app-debug.apk
├── build.gradle                           # 根构建脚本（AGP 8.1.4）
├── settings.gradle
├── gradle.properties
├── local.properties                       # SDK 路径（不提交版本控制）
├── gradle/wrapper/gradle-wrapper.properties
├── build_log.txt                          # 最近一次构建日志（失败，JDK 21 兼容性）
├── deploy_*.py                            # 前任开发者的部署脚本（Python）
├── modify_nr_*.py                         # 前任开发者的 APK 修改脚本
└── samplecode/ArcfaceDemo/               # ArcFace 官方示例（含完整 gradlew）
```

---

## 八、待确认问题（需问前任开发者）

如果以下问题影响构建，请向前任开发者确认：

1. **JDK 版本**：你之前构建时用的是 JDK 17 还是其他版本？安装路径在哪里？
2. **构建方式**：是用 Android Studio IDE 构建，还是命令行 `gradlew assembleDebug`？
3. **gradlew.bat 缺失**：项目根目录没有 `gradlew.bat` 和 `gradle-wrapper.jar`，是故意删除还是遗漏？
4. **deploy_*.py 脚本**：这些 Python 脚本是用来做什么的？是修改 APK 后重打包，还是辅助部署？
5. **ArcFace SDK**：SDK 的 so 库和 jar 包在哪里？是否需要单独下载？
6. **签名配置**：Release 构建的签名文件在哪里？Debug 构建是否足够？

---

## 九、参考链接

- [AGP 8.1 发布说明（JDK 17 要求）](https://developer.android.com/build/releases/gradle-plugin)
- [Eclipse Temurin JDK 17 下载](https://adoptium.net/temurin/releases/?version=17)
- [Gradle 8.4 文档](https://docs.gradle.org/8.4/release-notes.html)

---

*文档维护：TVPilot 项目 | 2026-09-08*
