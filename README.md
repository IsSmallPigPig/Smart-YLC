# 智慧团建管理员辅助（Qt 版）

PySide6 + PySide6-Fluent-Widgets 的桌面应用，替代 Electron 版（Electron 在本机存在无法修复的 Chromium 文字渲染故障）。

## 运行

```bash
pip install -r requirements.txt
python3 -m app.main
```

## 六页功能

| 页面 | 功能 |
|---|---|
| 工具首页 | 各功能入口卡片 |
| 命名检查 | 按「张三+13800000000+入团志愿书.pdf」校验符号/结构/重复/名单交叉，可一键改名 |
| 批量压缩 | PDF 压到 9.5MB（站点硬限 10MB），PyMuPDF 光栅化 + JPEG 梯度迭代，输出到「压缩结果」子目录 |
| 批量上传 | 站点分片协议上传（校验→分片→入库→自动审核），上传前需手输「确认上传」；审核调用网页端同款接口自动完成，失败时回退人工 |
| 批量检查 | 报到身份 / 报到情况 / 团员缴费 三项核对，异常导出 xlsx |
| 系统配置 | 团支部账号（config.json，`~/.gd12355-helper/`）、团员名单 xlsx 导入；「刷新所有数据」一键串行刷新 成员缓存 → 站内信 → 组织状况（添加账号后也自动触发，全程工作线程）；外观卡可切明暗模式（跟随系统 / 浅色 / 暗色，即时生效并持久化） |
| 登录闸门 | 会话复用（cookie 持久化，探测有效不登录）；登录要求验证码或触发风控 → 1 小时内跳过该账号的自动刷新；状态正常的账号数据 4 小时内刷新过则无需再刷新 |

## 测试

```bash
python3 -m pytest tests/ -q
```

41 个单元测试：命名规则、检查规则、登录协议（md5²）、分片协议、登录闸门，全部为纯函数/假客户端，不发网络。

## 协议来源

接口协议全部来自对 tuan.12355.net 的只读侦察（沿用 Electron 版结论）：
- 登录 GET `/login/adminLogin`，password = md5(md5(明文))，weakPass 标记
- 团员列表 POST `/api/v1/admin/members/page` + `authorize_name` 头
- 分片上传 `/file/breakpoint-upload`（10MB 分片）→ `/file/add` 入库
- 密码错一次触发验证码：先在网页端登录一次再使用上传功能

## 打包

```bash
pip install pyinstaller
pyinstaller -n 智慧团建管理员辅助 --windowed --onedir \
  --collect-all qfluentwidgets --collect-all PySide6 \
  app/main.py
```
