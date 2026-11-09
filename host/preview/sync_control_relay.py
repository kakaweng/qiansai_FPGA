"""Ubuntu relay: RK3568 owns commands; Windows owns the two file readers.

No model inference and no fabricated receiver acknowledgements in this process.
"""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

RETRY_ERRORS = (OSError, ValueError, KeyError, TypeError, urllib.error.URLError)


def waiting_message(error, config):
    if isinstance(error, FileNotFoundError):
        return ("RELAY_WAIT_CONFIG: 等待Windows视频源生成连接配置：" + str(config) +
                "。请先在Windows启动run_controlled_source.cmd并完成两路视频选择；此终端可保持运行。")
    if isinstance(error, json.JSONDecodeError):
        return "RELAY_WAIT_CONFIG: 连接配置尚未写完或内容无效，等待Windows视频源更新。"
    return ("RELAY_WAIT_CONNECTION: " + str(error) +
            "；请检查Windows视频源、VMware控制网络和板端9011端口转发。")


def wait_ready(relay, interval=1.0):
    """Allow any source/board startup order; never fabricate a ready state."""
    previous = None
    while True:
        try:
            relay.check()
            relay.last_error = None
            return
        except RETRY_ERRORS as error:
            message = waiting_message(error, relay.config)
            if message != previous:
                print(message, flush=True)
                previous = message
        time.sleep(interval)


def request(url, value=None, token=None, timeout=10):
    headers = {"X-Sync-Control": "1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if value is None else json.dumps(value).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    query = urllib.request.Request(url, data=data, headers=headers)
    # Both addresses are local device/control addresses: never route via system proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(query, timeout=timeout) as response:
        return json.load(response)


class Relay:
    def __init__(self, board, config, allow_local=False):
        self.board, self.config, self.allow_local = board.rstrip("/"), Path(config), allow_local
        self.last_error = None

    def source(self):
        config = json.loads(self.config.read_text(encoding="utf-8"))
        host = config["host"]
        if host in ("0.0.0.0", "::"):
            raise ValueError("源配置需要可从Ubuntu访问的Windows网卡地址，不能使用0.0.0.0")
        return f"http://{host}:{int(config['port'])}", config["token"]

    def check(self):
        address, token = self.source()
        source = request(address + "/status", token=token)
        board = request(self.board + "/api/status")
        if not source.get("network") and not self.allow_local:
            raise ValueError("视频源未启用FPGA网络输出，请退出--no-network模式")
        print(f"RELAY_CHECK_PASS board={board['state']} source={source['state']} address={address}", flush=True)

    def step(self):
        command = request(self.board + "/api/request", timeout=2)
        if not command["pending"]:
            return False
        address, token = self.source()
        source = request(address + "/status", token=token, timeout=2)
        if not source.get("network") and not self.allow_local:
            raise ValueError("Windows当前是本地预演模式，不能用于实机开始")
        result = request(address + "/command", command, token)
        if result.get("pending"):
            return False
        ok = result.get("ok") is True and result.get("session") == command["session"] and result.get("id") == command["id"]
        # Board rejects delayed replies for superseded commands.
        request(self.board + f"/api/ack?id={command['id']}&session={command['session']}&ok={int(ok)}", {})
        print(f"RELAY action={command['action']} session={command['session']} ok={ok}" +
              (" error=" + str(result.get("error")) if not ok else ""), flush=True)
        return True


def main():
    parser = argparse.ArgumentParser(description="RK3568开始指令 → Ubuntu USB/HDC中转 → Windows双路视频源")
    parser.add_argument("--board", default="http://127.0.0.1:9011")
    parser.add_argument("--source-config", type=Path,
                        default=Path(__file__).resolve().parent / "sync_playback_runs/source_control.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    relay = Relay(args.board, args.source_config)
    if args.check:
        try:
            relay.check()
        except RETRY_ERRORS as error:
            raise SystemExit(waiting_message(error, relay.config))
        return
    wait_ready(relay)
    print("RELAY_READY；保持此终端运行。请在板端控制面板点击开始。", flush=True)
    while True:
        try:
            relay.step()
            relay.last_error = None
        except RETRY_ERRORS as error:
            message = waiting_message(error, relay.config)
            if message != relay.last_error:
                print(message, flush=True)
                relay.last_error = message
        time.sleep(.15)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
