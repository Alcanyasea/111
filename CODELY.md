

## Codely Structured Memories

### User

### Feedback

### Project
- [2026-09-20 16:11:55] D:\1（明日方舟 MAA 挂机系统，远程 github.com/Alcanyasea/111 公开仓库）2026-09-20 完成容错加固：①master.lock 锁内容改为 "PID|进程启动UTC Ticks"、用 [IO.File]::Open(CreateNew) 原子抢锁（防 PID 复用永久挡死/并发双跑），gui/core/runner.py 与 scripts/export_operbox.py 的锁解析已兼容新旧两种格式；②新增 plugins/common.py 收敛五插件重复函数（load_config/find_account/read_json/maa_dir_for/atomic_json_write/log_file），插件用 sys.path.insert(parents[1]) 导入；③gui/core/maa_update.py 有崩溃恢复标记 RECOVER_MARKER（gui.new.json.update-recover），main.py 启动时 recover_all 自动复原；④base_schedule.plan_path_for_slot 对"清洗后≠原名"的槽位加 8 位 md5 后缀防计划文件互相覆盖；⑤两个启动挂机.bat 改为探测 pwsh 绝对路径（ProgramFiles→LocalAppData 别名→where）+%~dp0 相对路径，bat 内注释必须纯英文（cmd 解析 UTF-8 中文注释会拆行吞掉后续 set）；scheduler.py MASTER_PS1 按文件位置推导。已知待办（用户知情后暂缓）：PS 侧登录表单录入/配置加载 7 处拷贝去重、密码经 adb input text 明文、runner.stop 按镜像名杀 MAA.exe+无条件 shutdown /a、install.ps1 全局字符串替换路径、pytest 测试体系、vision.py 请求超时与失败缓存、release.yml 缓存。验证习惯：GUI --smoke 自检 + .scratch 临时仿真测试（用完即删）。
### Reference

