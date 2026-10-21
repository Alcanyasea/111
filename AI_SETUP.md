# AI 部署指南（新机器从零到可用）

> 本文件写给在新电脑上接手本仓库的 AI 助手（AGENTS 级说明）。目标：读完本文件
> + `README.md`，就能完成安装、补齐缺失组件并验证可用，不需要问人。
> 人看的完整文档在 `README.md`；本文只保留 AI 照做所需的最短路径与红线。

## 0. 这是什么

明日方舟 MAA 自动挂机系统：定时自动启动 MuMu 模拟器，在官服/B服多账号间自动
切换（非点击式槽位切号），逐个跑 MAA 完成每日任务，失败重试并推送通知。

四个组成部分：

| 部分 | 位置 | 语言 | 作用 |
|------|------|------|------|
| GUI 控制台 | `gui\` | Python (PySide6) | 人机界面：账号/班次/收菜/设置/历史/日志，管理 Windows 计划任务 |
| 脚本引擎 | `scripts\` | PowerShell 7 | 主流程：切号、登录校验（屏幕识别）、调度 MAA、重试、通知 |
| 插件 | `plugins\` | Python | 跑 MAA 前加工配置：精确基建、候选关卡、收菜、任务自检、推送 |
| 屏幕识别 | `scripts\vision\` | Python (OpenCV+ONNX) | 常驻识别进程 + MAA 同款模板/OCR 模型（已随仓库带齐，无需下载） |

## 1. 仓库里没有、必须现场补的东西

| 缺失项 | 补法 |
|--------|------|
| `config.json` | 复制 `config.example.json` 改名；或不管它——GUI 首次保存设置会自动生成，再在界面里填 |
| 账号密码 | **必须由用户本人提供**，在 GUI「账号管理」页输入（见 §2 第 7 步）。AI 不要代输入账号密码到命令行/代码/日志 |
| MuMu 模拟器 12 | https://mumu.163.com/ 下载安装；装好明日方舟官服 + B服两个客户端；分辨率设 **1280×720 横屏**（硬性要求，识别按 720p 基准） |
| MAA 两套（官服/B服） | https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases 下载 win-x64 解压。官服放 `D:\软件\MAA\MAA-v<版本>-win-x64\`（程序会自动探测取最新版）；再整体复制一份到 `D:\软件\MAA（b）\`，之后用 GUI 一键配置改成 Bilibili 客户端（见 §2 第 6 步，不用手动改） |
| Python 3.10+ | 建议 3.12（与 CI 一致）：`winget install Python.Python.3.12` |
| PowerShell 7 | 项目全部脚本按 PS7 语法写，5.1 跑不了：`winget install Microsoft.PowerShell` |
| Windows 计划任务 | 不用预建：GUI「仪表盘 → 班次计划」保存时自动创建/同步（需管理员权限运行 GUI 一次） |

## 2. 安装与配置步骤（可照抄）

以下命令默认在仓库根目录、PowerShell 7 下执行。

```powershell
# 1) 依赖：建 venv 并安装（PySide6/Fluent Widgets/pywin32 + opencv/onnxruntime）
python -m venv gui\.venv
gui\.venv\Scripts\python.exe -m pip install -r gui\requirements.txt

# 2) 初始化配置（GUI 也能自动生成，做了也不亏）
Copy-Item config.example.json config.json

# 3) 验证一：全量测试（应当全绿；本仓库写文档时为 134 个用例）
gui\.venv\Scripts\python.exe -m unittest discover -s tests
#   注意：必须用装了 GUI 依赖的解释器跑。系统 Python 没装 PySide6 时，
#   会有 4~6 个 GUI 测试模块报 loader error，那是环境问题不是代码问题。

# 4) 验证二：GUI 自检（加载全部页面后自动退出并打印 SMOKE OK）
gui\.venv\Scripts\python.exe gui\main.py --smoke
```

```powershell
# 5) 启动 GUI 控制台（后续操作都在界面里做）
gui\.venv\Scripts\python.exe gui\main.py
```

界面内的配置顺序：

6. **「运行设置」页**：检查 MAA/ADB 路径探测结果（官服 MAA 路径自动探测；
   B服默认 `D:\软件\MAA（b）\MAA.exe`）。然后分别点「MAA 服务器配置（官服）」
   和「（B服）」两个按钮——一键修正该服 MAA 的客户端类型、ADB 地址
   （MuMu 12 默认 `127.0.0.1:16384`）、RunDirectly、「任务结束后执行脚本」
   （自动指向本仓库 `scripts\signal_done.bat`）和常用任务开关。若 B服 MAA
   目录不存在会自动从官服复制一份再修正。
7. **「账号管理」页**：请用户本人输入游戏账号密码 →「开始捕获」。脚本自动
   清登录态 → 重启游戏 → 点掉首启弹窗 → 自动登录 → 拉取槽位数据。
   密码含特殊字符或弹验证码时自动转人工登录（提示用户在模拟器里手动登一次，
   脚本检测到成功会自动继续）。B服账号不捕获也能跑（仅切客户端）；
   官服账号必须捕获。
8. **「仪表盘 → 班次计划」**：设置每日启动时间（默认 4:00/16:00），保存时自动
   同步 Windows 计划任务 `MAA_明日方舟自动挂机`（此次保存需要管理员权限）。
   要定时收菜就再配「收菜计划」（任务名 `MAA_基建收菜`）。

## 3. 缺什么补什么（快速决策表）

| 现象 | 动作 |
|------|------|
| 没有 pwsh / python | §1 表格里的 winget 命令 |
| pip 装完仍有 GUI 测试 loader error | 用 `gui\.venv\Scripts\python.exe` 跑测试，别用系统 Python |
| `--smoke` 没输出 SMOKE OK | 无显示器环境加环境变量 `QT_QPA_PLATFORM=offscreen` 再试；仍失败看报错补依赖 |
| MAA 路径探测不到 | GUI「运行设置」手动填；或按 §1 建议的目录放 |
| 模拟器连不上 | MuMu 未开 / ADB 端口不是 16384 / 分辨率不是 1280×720 |
| GUI 报「已在运行」 | 单实例锁：关掉已开的控制台；崩溃残留锁在 `gui\console.lock`，下次启动自动清 |
| 提示「已在运行中」无法挂机 | 上轮未正常结束：确认没在跑后删 `scripts\master.lock` |
| 计划任务创建失败 | GUI 需以管理员身份运行一次 |

## 4. 改动红线（AI 必须遵守）

1. **测试/临时脚本绝不碰真实用户数据**：不读写真实 `config.json`、
   `scripts\accounts\`、日志、模拟器与 MAA 目录。用临时目录造假数据，并
   patch 掉落盘函数（参考 `tests\test_stage_plan_dialog.py` 的做法）。
2. **改动后必须验证**：全量测试（§2 第 3 步的命令）+ 受影响文件语法/导入
   检查，全绿才算完；有失败要说明是否与本次改动相关。
3. **覆盖/删除前先确认有备份可回退**（改配置文件前确认
   `config.json.daily.bak` 可用或先手动备份）。
4. **绝不手动增删改用户的 Windows 计划任务**——任务生命周期归 GUI
   班次/收菜计划页管，绕过 GUI 直接 `schtasks` 会破坏状态同步。
5. **新行为尽量加测试固化**，防止回归。
6. 终端命令优先 PowerShell 7（`pwsh`）。
7. 账号密码只允许进 `config.json` / 环境变量，不写进代码、测试、对话或文档。

## 5. 部署完成验证清单

- [ ] `gui\.venv\Scripts\python.exe -m unittest discover -s tests` 全绿
- [ ] `--smoke` 打印 `SMOKE OK`
- [ ] GUI「仪表盘」连接状态显示模拟器/MAA 检测通过
- [ ] 至少一个账号捕获成功（官服槽位数据生成在 `scripts\accounts\<slot>\`）
- [ ] 「运行设置 → MAA 服务器配置」两个按钮都执行成功
- [ ] 「立即运行」手动跑一轮：全部账号成功、运行历史页有记录
- [ ] 班次计划保存后，`schtasks /Query /TN "MAA_明日方舟自动挂机"` 能查到

## 6. 出问题去哪查

- 运行日志：`scripts\master_log.txt`（GUI「日志」页可直接看）
- 每轮结果：`scripts\run_history\`（GUI「运行历史」页，含逐号失败原因）
- 常见问题：`README.md` 的「常见问题」一节（覆盖执行策略、MAA 连接、超时、
  锁文件、首启弹窗等）
- 深入机制：`README.md` 的「执行流程」「账号槽位机制」「关键机制」三节
