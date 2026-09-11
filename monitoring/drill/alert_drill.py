#!/usr/bin/env python3
"""Alertmanager 告警投递演练。

两种模式：

  local   本机端到端演练。起一个本地 HTTP 接收器，用 `monitoring/alertmanager.yml`
          的真实配置启动 alertmanager，投递一条测试告警，断言接收器确实收到。
          需要本机有 alertmanager 可执行文件（用 --alertmanager-bin 指定）。
  remote  对已运行的 Alertmanager（例如生产容器）投递测试告警，用于验证
          真实通知渠道是否送达以及是否值得信任。

退出码：0 = 演练通过，1 = 演练失败。

真实配置文件的唯一改动：把 `url_file` 指向本机临时 secrets 目录。
其余部分（路由、分组、抑制规则、重复间隔、payload 模板）全部按仓库里的原样生效。

断言的是**企业微信群机器人**的负载契约，而不只是"有东西发出来"：
`amtool check-config` 不渲染模板，payload 写错只有真发一次才会暴露。
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "monitoring" / "alertmanager.yml"
DEFAULT_WORK = REPO_ROOT / "monitoring" / "drill" / "_work"
ALERT_NAME = "DrillTestAlert"

# 通知正文含 emoji，Windows 控制台默认是 GBK，直接 print 会抛 UnicodeEncodeError。
# 保留控制台编码、只把不可编码字符降级为 "?"，避免演练因展示问题而假失败。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http_json(url: str, payload, timeout: float = 10.0):
    """POST JSON，返回解析后的响应（无响应体则返回 None）。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def wait_ready(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0):
                return True
        except urllib.error.HTTPError:
            # 有 HTTP 响应即说明进程已在监听
            return True
        except Exception:
            time.sleep(0.5)
    return False


def build_alert(resolved: bool = False) -> list:
    now = datetime.now(timezone.utc)
    starts = now - timedelta(seconds=5)
    entry = {
        "labels": {"alertname": ALERT_NAME, "severity": "critical", "source": "drill"},
        "annotations": {
            "summary": "告警链路演练",
            "description": "由 monitoring/drill/alert_drill.py 投递，可忽略。",
        },
        "startsAt": starts.isoformat().replace("+00:00", "Z"),
        "generatorURL": "http://drill.local",
    }
    if resolved:
        entry["endsAt"] = now.isoformat().replace("+00:00", "Z")
    return [entry]


# --------------------------------------------------------------------------- #
# 本地接收器
# --------------------------------------------------------------------------- #
class _Sink(http.server.BaseHTTPRequestHandler):
    """接收 Alertmanager 投递的 webhook，把请求体存进 received。"""

    received: list = []
    lock = threading.Lock()

    def do_POST(self) -> None:  # noqa: N802 - http.server 接口约定
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        with type(self).lock:
            type(self).received.append(body)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args) -> None:
        pass


def received_count() -> int:
    with _Sink.lock:
        return len(_Sink.received)


def wechat_content(payload):
    """提取企业微信群机器人 markdown 正文；不符合约定则返回 None。

    自定义 payload 之后**不再有** `version` / `status` / `alerts` 等默认字段，
    群机器人只认 `{"msgtype":"markdown","markdown":{"content":"..."}}`。
    模板渲染出非法 JSON 时，Alertmanager 会把该值退化成字符串，
    群机器人随后报参数错误 —— 所以这里必须严格校验类型。
    """
    if not isinstance(payload, dict) or payload.get("msgtype") != "markdown":
        return None
    block = payload.get("markdown")
    if not isinstance(block, dict):
        return None
    content = block.get("content")
    return content if isinstance(content, str) and content else None


def one_line(text: str) -> str:
    return text.replace("\r", "").replace("\n", " | ")


def wait_for_sink(start_index: int, timeout: float):
    """等待下标为 start_index 的投递（即第 start_index+1 条），返回 (payload, 耗时秒)。"""
    begin = time.monotonic()
    deadline = begin + timeout
    while time.monotonic() < deadline:
        with _Sink.lock:
            if len(_Sink.received) > start_index:
                raw = _Sink.received[start_index]
                return json.loads(raw), time.monotonic() - begin
        time.sleep(0.5)
    return None, time.monotonic() - begin


# --------------------------------------------------------------------------- #
# local 模式
# --------------------------------------------------------------------------- #
def run_local(args) -> int:
    binary = (
        args.alertmanager_bin
        or os.environ.get("ALERTMANAGER_BIN")
        or shutil.which("alertmanager")
    )
    if not binary or not pathlib.Path(binary).exists():
        print(f"找不到 alertmanager 可执行文件（--alertmanager-bin）：{binary!r}", file=sys.stderr)
        return 1

    work = pathlib.Path(args.work_dir)
    if work.exists():
        shutil.rmtree(work)
    (work / "secrets").mkdir(parents=True)
    secrets_file = work / "secrets" / "wechat_robot_url"

    # 本地接收器
    sink_port = free_port()
    sink_url = f"http://127.0.0.1:{sink_port}/"
    secrets_file.write_text(sink_url + "\n", encoding="utf-8")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", sink_port), _Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[1/6] 本地接收器已监听 {sink_url}")

    # 用真实配置，仅改写 url_file 指向本机 secrets
    raw = pathlib.Path(args.config).read_text(encoding="utf-8")
    target = secrets_file.as_posix()
    rewritten, count = re.subn(
        r"(?m)^(\s*-\s*url_file:\s*).*$", lambda m: m.group(1) + target, raw
    )
    if count != 1:
        print(
            f"配置中 url_file 行匹配到 {count} 处（期望 1 处），演练脚本需同步更新",
            file=sys.stderr,
        )
        return 1
    cfg = work / "alertmanager.local.yml"
    cfg.write_text(rewritten, encoding="utf-8")
    print(f"[2/6] 已生成演练配置（仅 url_file 一行不同）：{cfg}")

    am_port = free_port()
    am_url = f"http://127.0.0.1:{am_port}"
    proc = subprocess.Popen(
        [
            binary,
            f"--config.file={cfg}",
            f"--storage.path={work / 'am-data'}",
            f"--web.listen-address=127.0.0.1:{am_port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    try:
        if not wait_ready(f"{am_url}/-/ready", timeout=30):
            err = b""
            if proc.poll() is not None and proc.stderr:
                err = proc.stderr.read()[:2000]
            print(f"[3/6] alertmanager 未就绪，退出码={proc.poll()}\n{err.decode(errors='replace')}", file=sys.stderr)
            return 1
        print(f"[3/6] alertmanager 已就绪 {am_url}（pid={proc.pid}）")

        # 关键：配置能被加载，本身就证明 url_file 注入生效
        print("[4/6] 投递测试告警（severity=critical，走 critical 路由）")
        expected_index = received_count()
        http_json(f"{am_url}/api/v2/alerts", build_alert())

        payload, elapsed = wait_for_sink(expected_index, timeout=args.timeout)
        if payload is None:
            print(
                f"[5/6] 失败：{args.timeout:.0f}s 内未收到投递。"
                "若配置的 group_wait 较长可调大 --timeout。",
                file=sys.stderr,
            )
            return 1

        content = wechat_content(payload)
        if content is None:
            print(
                "[5/6] 失败：投递不符合企业微信群机器人负载约定"
                "（payload 模板可能渲染成了非法 JSON）："
                f"{json.dumps(payload, ensure_ascii=False)[:400]}",
                file=sys.stderr,
            )
            return 1
        if ALERT_NAME not in content:
            print(f"[5/6] 失败：通知正文不含 {ALERT_NAME}：{content!r}", file=sys.stderr)
            return 1
        print(
            f"[5/6] 已收到投递：msgtype=markdown 耗时={elapsed:.1f}s\n"
            f"      正文：{one_line(content)}"
        )

        if args.check_resolved:
            print("[6/6] 投递恢复通知（send_resolved=true）…")
            expected_index = received_count()
            http_json(f"{am_url}/api/v2/alerts", build_alert(resolved=True))
            payload2, elapsed2 = wait_for_sink(expected_index, timeout=args.timeout)
            if payload2 is None:
                print(
                    f"[6/6] 失败：{args.timeout:.0f}s 内未收到恢复通知。"
                    "恢复通知受 group_interval（本配置 5m）节流，可把 --timeout 调到 360 以上重试。",
                    file=sys.stderr,
                )
                return 1
            content2 = wechat_content(payload2)
            if content2 is None or "已恢复" not in content2:
                print(
                    f"[6/6] 失败：恢复通知未按预期渲染（应含“已恢复”）：{content2!r}",
                    file=sys.stderr,
                )
                return 1
            print(
                f"[6/6] 已收到恢复通知 耗时={elapsed2:.1f}s\n"
                f"      正文：{one_line(content2)}"
            )
        else:
            print("[6/6] 跳过恢复通知检查（加 --check-resolved 启用；受 group_interval 影响较慢）")

        print("\n演练通过：告警 -> 路由 -> 真实投递端点 全链路可用。")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        server.shutdown()
        print("已清理演练进程与本地接收器")


# --------------------------------------------------------------------------- #
# remote 模式
# --------------------------------------------------------------------------- #
def run_remote(args) -> int:
    base = args.alertmanager_url.rstrip("/")
    if not wait_ready(f"{base}/-/ready", timeout=10):
        print(f"Alertmanager 不可达：{base}/-/ready", file=sys.stderr)
        return 1

    action = "恢复通知" if args.resolve else "测试告警"
    try:
        http_json(f"{base}/api/v2/alerts", build_alert(resolved=args.resolve))
    except urllib.error.HTTPError as exc:
        print(f"投递失败 HTTP {exc.code}: {exc.read()[:500].decode(errors='replace')}", file=sys.stderr)
        return 1

    print(f"已向 {base} 投递{action}（alertname={ALERT_NAME}, severity=critical）")
    print("请在真实接收端（企业微信/钉钉/邮件/网关）确认收到，并记录到达时间用于核对延迟。")
    if not args.resolve:
        print("确认后运行同一命令并加 --resolve，可验证恢复通知是否送达。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Alertmanager 告警投递演练")
    parser.add_argument("--mode", choices=["local", "remote"], default="local")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="alertmanager 配置文件")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK), help="演练临时目录")
    parser.add_argument("--alertmanager-bin", default=None, help="alertmanager 可执行文件路径")
    parser.add_argument("--alertmanager-url", default="http://127.0.0.1:9093", help="remote 模式的地址")
    parser.add_argument("--timeout", type=float, default=90.0, help="等待投递的秒数")
    parser.add_argument("--check-resolved", action="store_true", help="额外校验恢复通知")
    parser.add_argument("--resolve", action="store_true", help="remote 模式投递恢复通知")
    args = parser.parse_args()

    try:
        return run_local(args) if args.mode == "local" else run_remote(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
