# 优化清单

> 用途：记录待优化项、已验证结论和踩过的坑，供后续迭代参考。
> 约定：每条尽量写清「现象 → 原因 → 建议 → 怎么验收」，避免凭猜测改动。
> 最近更新：2026-11-03（v2.1.2 之后的主控重构收尾阶段）

---

## 一、当前状态（事实基线）

| 项目 | 状态 | 证据 |
|---|---|---|
| 单元测试 | 241 项全绿 | `gui\.venv\Scripts\python.exe -m unittest discover -s tests -t .` |
| Python 运行时 | 3.12.10（与 CI 一致） | `gui\.venv\pyvenv.cfg`；旧环境备份在 `gui\.venv-py314rc3.bak`（911 MB，确认无误后可删） |
| GUI 自检 | 通过 | `python gui/main.py --smoke` → `SMOKE OK` |
| 切号链路 | 真机通过 | 2026-11-03 01:53，`-SwitchTo official_1 -NoLoginCheck`，停游戏→推 3 文件→启动→uid 校验，7.5 秒 |
| 失败现场归档 | 真机命中 2 次 | `run_history\20261103_015956\official_1\`、`run_history\20261103_023212\official_1\`，日志含 `[证据] 登录校验 失败现场已归档（2 个文件）` |
| 屏幕识别 | 正常 | `vision.py --image ... --ops ...`，0.67 秒识别出截图全部文字 |
| **收菜全链路** | **未验证** | 见下方 P0 |

---

## 二、P0：收菜链路尚未真机验证（发布前必须补）

**现象**：`master.ps1 -InfrastCollect` 的完整链路（切号 → 登录校验 → 收菜插件 → MAA → 回拉）没有在正常环境跑通过一次。

**原因**：本次验证是在 AI 工具的执行沙箱里跑的，沙箱拦截 `sc.exe`，导致模拟器只能以无头实例启动（详见第四节）。不是代码问题。

**验收标准**：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File D:\1\scripts\master.ps1 -InfrastCollect -NoShutdown
```

- 4 个账号全部 `[OK]`
- 结束出现 `ALL ACCOUNTS COMPLETED SUCCESSFULLY`
- `scripts\run_history\run_<时间戳>.json` 中 `mode=collect`、`failed=0`
- 日志中每个号都有 `[基建收菜] OK` 与「Current 已切回 Default」

---

## 三、P1：可靠性与运维

### 3.1 日志出现双时间戳

**现象**：

```
2026-11-02 22:16:12 -   2026-11-02 22:16:12 -   [Refresh] slot 'bilibili_1' updated
```

**原因**：子脚本（`refresh_slot.ps1` / `slot_switch.ps1`）自己的输出已带时间戳，`Run-Switch` 逐行读取后用 `Log` 又包了一层。

**建议**：`master_process.ps1` 的读取循环里判断行首是否已匹配 `^\d{2}:\d{2}:\d{2}\s`，已带时间戳就不再前缀；或统一让子脚本只输出纯文本。

**验收**：任意一次运行的日志中，`[Switch]` / `[Refresh]` 行只有一个时间戳。

**结论（2026-11-03）**：已采用。主控移除子脚本输出行首的完整日期或 `HH:mm:ss` 时间戳，再统一使用原有 `Log` 落盘；每行仍保留 GUI 要求的完整日期与主控记录时刻，普通正文和 stderr 标记保持不变。回拉脚本增加 `-NoLogFile`，由主控调用时启用，避免脚本自身再写一份日志。

### 3.2 失败现场归档的容量没有上限

**现状**：每个失败号最多归档 3 个文件，跟着运行历史保留 60 天 / 最多 400 轮。

**估算**：单张截图约 300 KB，最坏情况（每轮 4 个号全失败）约 3.6 MB/轮，60 天按每天 2 轮算约 430 MB。目前已有 2 个证据目录（`20261103_015956`、`20261103_023212`），量级还很小。

**建议**：给 `run_history` 下的证据目录加总容量上限（例如 200 MB，超了先删最老的），或在 GUI 运行历史页显示「证据占用」并支持一键清理。

**验收**：构造 60 天历史后，证据目录总量不超过上限。

### 3.3 运行历史没有趋势视图

**现状**：238 个 JSON 里已有逐号耗时、成败、失败步骤，但只能一轮一轮看。

**建议**：在「运行历史」页顶部加一个近 30 天的成功率 / 平均耗时折线（纯前端，数据现成）。哪个号在悄悄变慢，一眼能看出来。

### 3.4 密码明文

**现状**：`config.json` 里账号密码明文；登录时通过 `adb input text` 传递，进程命令行可见。

**建议**：用 Windows DPAPI（`ProtectedData.Protect`，CurrentUser 范围）加密存储，运行时解密。改动集中在 `gui/config.py` 的读写和 `login_device_lib.ps1` 的取用。

**注意**：会导致旧配置需要一次性迁移，迁移失败要能回退到明文读取。

---

## 四、环境事故记录（2026-11-03，避免重复踩）

### 4.1 现象

- 跑 `master.ps1` 后模拟器窗口不出现，任务管理器里只有 `MuMuVMMHeadless.exe`，没有 `MuMuNxDevice.exe`
- `adb shell dumpsys display | grep -c DisplayDeviceInfo` 返回 **3**（正常应为 1），多出两个 `mumuscreen007/008` 的 HDMI 虚拟屏
- 启动游戏后，`screencap` 抓到的永远是桌面，但游戏进程存活、`mCurrentFocus` 指向游戏窗口
- 登录校验因此每轮空转 240 秒后超时

### 4.2 原因

AI 工具的执行沙箱有程序黑名单，会拦截 `sc.exe`：

```
PROGRAM BLOCKED BY SECURITY POLICY - sc.exe (C:\WINDOWS\SYSTEM32\sc.exe)
```

`mumu-cli control launch` 启动虚拟机时依赖它，被拦后模拟器退化为无头实例。无头实例的渲染路径会创建虚拟 HDMI 屏，游戏画面不在 display 0 上。

### 4.3 判断方法

```powershell
Get-Process | Where-Object { $_.ProcessName -like "*MuMu*" }
```

- 有 `MuMuNxDevice.exe` → 正常（带设备窗口）
- 只有 `MuMuVMMHeadless.exe` → 无头实例，画面抓不到

### 4.4 恢复方法

1. 结束所有 MuMu 进程（保留 `MuMuRemoteService` / `MuMuRemoteBackend` / `MumuRemoteHealthd` 三个系统服务）
2. 双击或启动 `MuMuNxMain.exe`，**等窗口稳定驻留 40 秒以上**
3. 再让模拟器启动设备（界面点「启动」，或 `mumu-cli control -v 0 launch`）
4. 确认 `dumpsys display` 只返回 1 个 DisplayDeviceInfo

### 4.5 教训

**不要用 AI 工具直接执行会启动模拟器的脚本**（`master.ps1`、`启动挂机.bat`）。需要真机验证时，由人在正常桌面环境启动，AI 只读日志和运行历史。

### 4.6 本次会话动过的系统设置

- `renderer_mode`：曾从 `vk` 改为 `dx` 用于排查，**已改回 `vk`**
- 其余模拟器设置未改动

---

## 五、P2：架构与配置

### 5.1 config 有三套读取、九处默认值

| 位置 | 职责 |
|---|---|
| `gui/config.py` | 默认值 + 迁移 + 保存 |
| `plugins/common.py` | 插件读取 |
| `scripts/config_lib.ps1` | PS 侧读取 |

另外 `127.0.0.1:16384` 在 8 个 PS 脚本里各硬编码一遍（`slot_switch` / `login_check` / `login_check_bilibili` / `capture_account` / `refresh_slot` / `master_lib` / `switch_to_B服` / `switch_to_官服`）。

**风险**：改默认值或加字段时漏一处，就是「只在某个班次炸」的 bug。

**建议**：以 `config.example.json` 作为唯一 schema 真相，GUI 保存时按它校验，PS 侧只读不设默认值。

### 5.2 PowerShell 测试的覆盖边界

`tests/ps_harness.py` 用 AST 只抽取函数定义来测试，跨文件的**作用域依赖**测不出来。

本次已踩过一次：`Save-FailureEvidence` 最初放在 `master_history.ps1`，而 `test_master_entry.py` 只加载 pipeline，5 个用例直接红。

**建议**：新增 PS 函数时，默认放在调用方所在文件；跨文件依赖要在文件头注释里写明「依赖哪个文件先 dot-source」。

### 5.3 归档函数的隐式状态

`Save-FailureEvidence` 通过 `$script:EvidenceSlot` / `$script:EvidenceServer` 获取当前账号，由 `Invoke-AccountRun` 开头写入。

**取舍**：这样避免了改 6 处失败分支的调用签名，但隐式状态对后续维护不友好。

**建议**：如果以后 pipeline 结构再变，改为显式传参（`New-AccountResult ... -Slot $slot -Server $server`）。

### 5.4 运行历史目录名与记录时间的耦合

`gui/pages/history.py` 的 `_evidence_dir()` 用记录里的 `start` 推出目录名（`%Y%m%d_%H%M%S`），依赖它和 `master_lib.ps1` 的 `$runStamp` 同源。

**风险**：任何一方改了时间格式，按钮就会静默消失。

**建议**：在 `run_*.json` 里显式写入 `stamp` 字段，前端直接读，不再反推。

**结论（2026-11-03）**：已采用。新记录写入 `stamp`，历史页优先读取；旧记录继续从 `start` 回退推导。

---

## 六、P3：体验与大工程

- **README 拆分**：40 KB 单文件，建议拆成 `docs/架构.md`、`docs/排班.md`、`docs/故障排查.md`，README 只留索引。
- **并行跑号**：4 个号串行，收菜约 16 分钟、日常更久。MAA 支持多开，理论上双模拟器实例可减半，但切号机制建立在「单设备、单 ADB、单 MAA 配置」上，等于重做一半调度层。**除非每天真的在等这十几分钟，否则不建议动。**
- **控制台自身版本检查**：MAA 有更新检查，控制台没有。

---

## 七、待办速查

- [ ] P0 收菜全链路真机验证（正常桌面环境）
- [x] P1.1 修日志双时间戳
- [ ] P1.2 证据目录容量上限
- [ ] P1.3 运行历史趋势图
- [ ] P1.4 密码 DPAPI 加密
- [ ] P2.1 config 单一 schema
- [x] P2.4 `run_*.json` 增加 `stamp` 字段
- [ ] P3 README 拆分
