# 智慧团委 · 手机端（原生）

Android 原生端，基于 [KernelSU Style UI Kit](https://github.com/chenaizhang/KernelSU-Style-UI-Kit) 模板改造，使用 Miuix 组件。只覆盖桌面端除「内嵌浏览器访问支部」之外的能力，并把纸质志愿书扫描做成了完整的拍照 → 裁切 → 命名 → PDF → 上传链路。

## 页面

底部五个标签：首页、名单、扫描、消息、我的。

- **首页**：上传进度、四类异常分布、未读消息入口、账号与同步状态。
- **名单**：团员搜索与筛选，离线可读本地缓存；点团员可直接为该团员扫描。
- **扫描**：拍照或从相册导入 → 多页预览 → 逐页拉四角/旋转/滤镜 → 团员模糊搜索补齐文件名 → 生成 PDF。页面保存在本机扫描件库，可打开、分享、上传。
- **消息**：站内信按报到/转接/其他分类。
- **我的**：账号、扫描默认参数、主题、关于。

## 站点协议

Kotlin 侧移植自桌面端 `app/services/api.py` 与 `app/domain/*`：登录（double-MD5、验证码）、`getSessionAccount`、组织树、团员分页、站内信、`/file/breakpoint-upload` 分片上传、`/file/add` 入库与 `auditVoluntaryBook` 审核。Cookie 按账号分别持久化，切换账号不顶掉另一个登录态。

命名规则与桌面端一致：`姓名+手机号+入团志愿书.pdf`。PDF 用 `PdfDocument` 思路的直接 JPEG 流封装，生成时按目标体积自动降质量/分辨率，满足站点单文件 10MB 上限。

## 构建

JDK、Gradle、Android SDK 与依赖缓存均在移动硬盘。构建产物也写到移动硬盘，避免占用本机系统盘：

```bash
source /Volumes/AnkoQAQ/kotlin-dev/env.sh
cd mobile
./gradlew :app:assembleDebug
```

产物：`/Volumes/AnkoQAQ/kotlin-dev/build/smart-youth/_app/outputs/apk/debug/app-debug.apk`

## 说明

- 桌面端 `app/` 是 PyQt5 应用，与手机端共用站点协议规则，但不共用代码。手机端没有内嵌浏览器。
- 账号密码与会话 Cookie 只存在应用私有目录；扫描件存在 `getExternalFilesDir("documents")/scan`，卸载应用时清除。
- 纸边检测依赖 OpenCV。加载失败时自动退化为整幅图加内边距，仍可手动拉四角。
