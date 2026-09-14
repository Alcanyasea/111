# -*- coding: utf-8 -*-
r"""通知推送插件：把一条消息推送到手机（Server酱 / PushPlus / 企业微信机器人）。

发送逻辑全项目只有这一份，两个调用方：
- master.ps1：每轮挂机/收菜结束按 config.json 的 notify 配置推送结果
  （失败必推，「成功也推送」开关控制成功是否推）：
      python notify.py send --config D:\1\config.json --title T --body B
- 控制台「运行设置 → 通知推送 → 发送测试」：直接传渠道与密钥，不落盘：
      python notify.py send --provider serverchan --key XXX --title T --body B

渠道与密钥格式：
- serverchan  Server酱·Turbo（sct.ftqq.com）  key = SendKey（SCT 开头）
- pushplus    PushPlus（pushplus.plus）       key = token
- wecom       企业微信群机器人                 key = webhook 整条地址或其 key 参数

退出码：0 = 已推送（--config 方式下未启用也返回 0）；1 = 推送失败（网络/密钥）；
2 = 渠道或密钥参数无效。
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = Path(r"D:\1\config.json")
TIMEOUT = 15          # 单次 HTTP 超时（秒）：推送慢不该拖住收尾流程
PROVIDERS = ("serverchan", "pushplus", "wecom")


def load_notify(path):
    """读 config.json 的 notify 节；文件/字段缺失返回空 dict（视为未启用）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    n = cfg.get("notify")
    return n if isinstance(n, dict) else {}


def _post(url, data, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _post_json(url, payload):
    return _post(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                 {"Content-Type": "application/json"})


def send(provider, key, title, body):
    """按渠道推送，返回 (ok, 结果消息)。网络异常向上抛，由调用方统一报错。"""
    provider = (provider or "").strip().lower()
    key = (key or "").strip()
    if provider not in PROVIDERS:
        return False, "未知渠道：%r（支持 %s）" % (provider, "/".join(PROVIDERS))
    if not key:
        return False, "推送密钥为空，请在「运行设置 → 通知推送」填写"

    if provider == "serverchan":
        # Server酱·Turbo：title 超长会被服务端截断，这里先自截到 32 字符
        res = _post("https://sctapi.ftqq.com/%s.send" % key,
                    urllib.parse.urlencode({"title": title[:32], "desp": body}).encode("utf-8"))
        ok = res.get("code") == 0
        return ok, ("Server酱：已推送" if ok else "Server酱：%s" % (res.get("message") or res.get("code")))

    if provider == "pushplus":
        res = _post_json("https://www.pushplus.plus/send",
                         {"token": key, "title": title[:100],
                          "content": body, "template": "txt"})
        ok = res.get("code") == 200
        return ok, ("PushPlus：已推送" if ok else "PushPlus：%s" % (res.get("msg") or res.get("code")))

    # wecom 企业微信群机器人：key 允许传整条 webhook 地址，从中取 key 参数
    if key.lower().startswith("http"):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(key).query)
        key = (qs.get("key") or [""])[0]
    if not key:
        return False, "企业微信 webhook 地址无效（缺少 key 参数）"
    # 机器人 text 消息上限 2048 字节（UTF-8），预留余量截断
    content = "%s\n%s" % (title, body)
    while len(content.encode("utf-8")) > 1800:
        content = content[:-100]
    res = _post_json("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=%s" % key,
                     {"msgtype": "text", "text": {"content": content}})
    ok = res.get("errcode") == 0
    return ok, ("企业微信：已推送" if ok else "企业微信：%s" % (res.get("errmsg") or res.get("errcode")))


def main():
    # 中文推到管道时个别字符可能不在 GBK 里：替换而不是让 print 崩掉
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="MAA 挂机通知推送")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("send", help="发送一条通知")
    sp.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help="config.json 路径（未传 --provider 时从中读 notify 节）")
    sp.add_argument("--provider", default="", choices=("",) + PROVIDERS)
    sp.add_argument("--key", default="")
    sp.add_argument("--title", required=True)
    sp.add_argument("--body", default="")
    args = ap.parse_args()

    provider, key = args.provider, args.key
    if not provider:
        # master.ps1 方式：配置里没启用直接放行（退出 0，不算失败）
        n = load_notify(args.config)
        if not n.get("enabled"):
            print("[通知] 未启用推送，跳过")
            return 0
        provider = str(n.get("provider") or "")
        key = str(n.get("key") or "")

    try:
        ok, msg = send(provider, key, args.title, args.body)
    except Exception as exc:
        print("[通知] 推送失败：%s" % exc)
        return 1
    print(("[通知] %s" % msg) if ok else ("[通知] 推送失败：%s" % msg))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
