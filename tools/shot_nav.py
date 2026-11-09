"""截图核对：全站导航栏不再出现「排行榜 / 错题本」，个人中心仍保留两个入口。

前置：站点已启动（默认 http://localhost:5000）+ 已安装 websocket-client。
运行：python tools/shot_nav.py
输出：系统临时目录下 nav_profile.png（个人中心首屏）与 nav_home.png（首页首屏）。
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = "http://127.0.0.1:5000"
PORT = 9345
OUT_DIR = os.path.join(tempfile.gettempdir(), "daily_quiz_shots")


def wait_json(path, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("Chrome 调试端口未就绪")


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=20)
        self.seq = 0

    def send(self, method, **params):
        self.seq += 1
        mid = self.seq
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})

    def js(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def goto(self, url, wait=1.2):
        self.send("Page.navigate", url=url)
        time.sleep(wait)

    def shot(self, name, clip=None):
        params = {"format": "png", "captureBeyondViewport": False}
        if clip:
            params["clip"] = dict(clip, scale=1)
        data = self.send("Page.captureScreenshot", **params)["data"]
        path = os.path.join(OUT_DIR, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    profile_dir = tempfile.mkdtemp(prefix="cdp_shot_")
    proc = subprocess.Popen(
        [
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--remote-debugging-port=%d" % PORT,
            "--user-data-dir=%s" % profile_dir,
            "--window-size=1440,900", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        targets = wait_json("/json/list")
        page = next(t for t in targets if t["type"] == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride",
                 width=1440, height=900, deviceScaleFactor=1, mobile=False)

        # 登录（提交会触发页面跳转，evaluate 可能拿不到返回值，忽略即可）
        cdp.goto(BASE + "/login")
        try:
            cdp.js("""(() => {
                document.querySelector('#username').value = 'user';
                document.querySelector('#password').value = 'user123';
                document.querySelector('form').submit();
                return true;
            })()""")
        except RuntimeError:
            pass
        time.sleep(2)

        # 个人中心
        cdp.goto(BASE + "/profile", wait=2)
        nav_text = cdp.js("document.querySelector('nav.nav').innerText")
        hero_btns = cdp.js("""Array.from(document.querySelectorAll('.profile-actions a')).map(a => a.innerText.trim())""")
        overflow = cdp.js("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        path_profile = cdp.shot("nav_profile.png")

        # 首页
        cdp.goto(BASE + "/", wait=1.8)
        home_nav = cdp.js("document.querySelector('nav.nav').innerText")
        path_home = cdp.shot("nav_home.png", clip={"x": 0, "y": 0, "width": 1440, "height": 160})

        # 窄屏（504px）检查横向溢出
        cdp.send("Emulation.setDeviceMetricsOverride",
                 width=504, height=900, deviceScaleFactor=1, mobile=False)
        cdp.goto(BASE + "/profile", wait=1.5)
        mobile_overflow = cdp.js("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        mobile_nav = cdp.js("document.querySelector('nav.nav').innerText")
        path_mobile = cdp.shot("nav_mobile.png", clip={"x": 0, "y": 0, "width": 504, "height": 420})

        print("导航栏文本（个人中心页）:", repr(nav_text))
        print("导航栏文本（首页）      :", repr(home_nav))
        print("个人中心 hero 按钮       :", hero_btns)
        print("横向溢出像素（1440）     :", overflow)
        print("横向溢出像素（504）      :", mobile_overflow)
        print("窄屏导航                 :", repr(mobile_nav))
        print("截图：", path_profile)
        print("截图：", path_home)
        print("截图：", path_mobile)

        ok = True
        for label in ("排行榜", "错题本"):
            if label in (nav_text or ""):
                print("FAIL 导航栏仍出现:", label)
                ok = False
        for label in ("排行榜", "错题本"):
            if label not in (hero_btns or []):
                print("FAIL 个人中心缺少入口:", label)
                ok = False
        if overflow and overflow > 0:
            print("FAIL 桌面端存在横向溢出")
            ok = False
        if mobile_overflow and mobile_overflow > 0:
            print("FAIL 窄屏存在横向溢出")
            ok = False
        for label in ("排行榜", "错题本"):
            if label in (mobile_nav or ""):
                print("FAIL 窄屏导航仍出现:", label)
                ok = False
        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
