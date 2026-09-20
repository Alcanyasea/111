

## Codely Structured Memories

### User

### Feedback

### Project
- [2026-09-20 16:11:55] D:\1（明日方舟 MAA 挂机系统，远程 github.com/Alcanyasea/111 公开仓库）2026-09-20 完成容错加固：①master.lock 锁内容改为 "PID|进程启动UTC Ticks"、用 [IO.File]::Open(CreateNew) 原子抢锁（防 PID 复用永久挡死/并发双跑），gui/core/runner.py 与 scripts/export_operbox.py 的锁解析已兼容新旧两种格式；②新增 plugins/common.py 收敛五插件重复函数（load_config/find_account/read_json/maa_dir_for/atomic_json_write/log_file），插件用 sys.path.insert(parents[1]) 导入；③gui/core/maa_update.py 有崩溃恢复标记 RECOVER_MARKER（gui.new.json.update-recover），main.py 启动时 recover_all 自动复原；④base_schedule.plan_path_for_slot 对"清洗后≠原名"的槽位加 8 位 md5 后缀防计划文件互相覆盖；⑤两个启动挂机.bat 改为探测 pwsh 绝对路径（ProgramFiles→LocalAppData 别名→where）+%~dp0 相对路径，bat 内注释必须纯英文（cmd 解析 UTF-8 中文注释会拆行吞掉后续 set）；scheduler.py MASTER_PS1 按文件位置推导。已知待办（用户知情后暂缓）：PS 侧登录表单录入/配置加载 7 处拷贝去重、密码经 adb input text 明文、runner.stop 按镜像名杀 MAA.exe+无条件 shutdown /a、install.ps1 全局字符串替换路径、pytest 测试体系、vision.py 请求超时与失败缓存、release.yml 缓存。验证习惯：GUI --smoke 自检 + .scratch 临时仿真测试（用完即删）。
- [2026-09-20 17:32:09] D:\1 第二批（2026-09-20 提交 aaa0d48）：runner.stop 仅在锁确认挂机在跑时 taskkill 树杀+兜底 /IM MAA+shutdown /a（挂机没跑时绝不碰 MAA 与关机计划）；proc.py 新增 snapshot()/process_start_ticks()（FILETIME→.NET ticks 换算常数 504911232000000000，已与 PowerShell StartTime.Ticks 跨语言核对）；capture_account.ps1 凭据只走 MAA_CAPTURE_USERNAME/PASSWORD 环境变量（-Username/-Password 参数已移除，GUI accounts.py 本就传环境变量）；install.ps1 目标路径危险字符校验+改写后语法自检+计划任务改 Register-ScheduledTask 单任务直指 master.ps1；PS 侧去重落地：scripts/config_lib.ps1（Read-AppConfigJson 显式传路径）替换 8 处 config 读取前导，Type-Field/SAFE_CHARS 收敛进 login_device_lib.ps1；tests/ 35 个 unittest 用例（run_tests.ps1 一键跑，CI release.yml 已加 test job+缓存），写死 D:\1 的测试必须改成临时目录+相对路径否则 CI 会挂。current_batch 契约：entries 必须升序（schedule_spec 产出已升序），乱序传入 00:00 会误判批次。

### Reference

