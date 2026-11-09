"""截图核对：登录验证码、注册页用户名实时校验、个人中心 DiceBear 头像。

前置：站点已启动（默认 http://localhost:5000）+ 已安装 websocket-client。
运行：python tools/shot_auth.py
输出：系统临时目录 daily_quiz_shots/ 下的 auth_*.png 与 RESULT: PASS/FAIL。
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
PORT = 9346
OUT_DIR = os.path.join(tempfile.gettempdir(), "daily_quiz_shots")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def redis_get(key):
    out = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", "--raw", "get", key],
        cwd=PROJECT,
    )
    return out.decode("utf-8").strip()


def clear_rate_limits():
    """截图会反复取码，先清掉同 IP 的取码限流计数，避免脚本自己被 429 拦住。"""
    subprocess.run(
        ["docker", "compose", "exec", "-T", "redis", "sh", "-c",
         "redis-cli --scan --pattern 'login:captcha:rate:*' | xargs -r redis-cli del"],
        cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )


def issue_captcha():
    with urllib.request.urlopen(BASE + "/api/auth/captcha", timeout=10) as resp:
        data = json.loads(resp.read().decode())["data"]
    return data["captchaKey"], redis_get("login:captcha:" + data["captchaKey"])


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    clear_rate_limits()
    profile_dir = tempfile.mkdtemp(prefix="cdp_auth_")
    proc = subprocess.Popen(
        [
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--remote-debugging-port=%d" % PORT,
            "--user-data-dir=%s" % profile_dir,
            "--window-size=1440,900", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = True
    try:
        targets = wait_json("/json/list")
        page = next(t for t in targets if t["type"] == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        cdp.send("Emulation.setDeviceMetricsOverride",
                 width=1440, height=900, deviceScaleFactor=1, mobile=False)

        # ---- 登录页：验证码渲染
        cdp.goto(BASE + "/login", wait=2)
        has_captcha = cdp.js("""(() => {
            const img = document.querySelector('#captcha-img');
            const key = document.querySelector('#captcha-key');
            return !!(img && key && img.src.startsWith('data:image/svg+xml;base64,') && key.value);
        })()""")
        path_login = cdp.shot("auth_login.png", clip={"x": 300, "y": 0, "width": 840, "height": 720})
        print("登录页验证码渲染:", has_captcha)
        if not has_captcha:
            print("FAIL 登录页没有渲染验证码")
            ok = False

        # ---- 错误验证码：提示 + 自动换码
        old_src = cdp.js("document.querySelector('#captcha-img').src")
        cdp.js("""(() => {
            document.querySelector('#username').value = 'user';
            document.querySelector('#password').value = 'user123';
            document.querySelector('#captcha').value = 'ZZZZ';
            document.querySelector('#login-form').requestSubmit();
            return true;
        })()""")
        time.sleep(2.2)
        login_error = cdp.js("(document.querySelector('#form-error')||{}).textContent || ''")
        new_src = cdp.js("document.querySelector('#captcha-img').src")
        kept_user = cdp.js("document.querySelector('#username').value")
        path_error = cdp.shot("auth_login_error.png", clip={"x": 300, "y": 0, "width": 840, "height": 720})
        print("错误提示:", login_error, "| 已换码:", old_src != new_src, "| 保留用户名:", kept_user)
        if "验证码" not in (login_error or ""):
            print("FAIL 验证码错误提示缺失")
            ok = False
        if old_src == new_src:
            print("FAIL 失败后没有刷新验证码")
            ok = False

        # ---- 注册页：已占用用户名（红）
        cdp.goto(BASE + "/register", wait=2.6)
        cdp.js("document.querySelector('#username').value = 'user'")
        # headless 下窗口可能无系统焦点，focus/blur 不派发事件；直接派发 blur 事件
        cdp.js("document.querySelector('#username').dispatchEvent(new FocusEvent('blur'))")
        time.sleep(2.0)
        taken_msg = cdp.js("document.querySelector('#username-msg').textContent")
        taken_cls = cdp.js("document.querySelector('#username-msg').className")
        taken_input = cdp.js("document.querySelector('#username').className")
        path_taken = cdp.shot("auth_register_taken.png", clip={"x": 300, "y": 0, "width": 840, "height": 860})
        print("占用提示:", taken_msg, "| class:", taken_cls, "| input:", taken_input)
        if "已被注册" not in (taken_msg or "") or "is-err" not in (taken_cls or ""):
            print("FAIL 占用用户名未显示红色提示")
            ok = False

        # ---- 注册页：可用用户名（绿）
        probe = "probe_%d" % int(time.time() % 100000)
        cdp.js("document.querySelector('#username').value = '%s'" % probe)
        # headless 下窗口可能无系统焦点，focus/blur 不派发事件；直接派发 blur 事件
        cdp.js("document.querySelector('#username').dispatchEvent(new FocusEvent('blur'))")
        time.sleep(2.0)
        free_msg = cdp.js("document.querySelector('#username-msg').textContent")
        free_cls = cdp.js("document.querySelector('#username-msg').className")
        path_free = cdp.shot("auth_register_free.png", clip={"x": 300, "y": 0, "width": 840, "height": 860})
        print("可用提示:", free_msg, "| class:", free_cls)
        if "可以使用" not in (free_msg or "") or "is-ok" not in (free_cls or ""):
            print("FAIL 可用用户名未显示绿色提示")
            ok = False

        # ---- 登录成功 → 个人中心头像
        key, code = issue_captcha()
        cdp.goto(BASE + "/", wait=1)
        login_result = cdp.js("""fetch('/api/auth/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({username: 'user', password: 'user123',
                                  captchaKey: '%s', captchaCode: '%s'})
        }).then(r => r.json())""" % (key, code))
        print("登录接口:", login_result)
        if not (login_result or {}).get("ok"):
            print("FAIL 浏览器登录失败")
            ok = False

        cdp.goto(BASE + "/profile", wait=3)
        avatar = cdp.js("""(() => {
            const img = document.querySelector('.profile-avatar .avatar-img');
            return img ? {src: img.src, complete: img.complete, w: img.naturalWidth, h: img.naturalHeight} : null;
        })()""")
        fallback = cdp.js("document.querySelector('.profile-avatar').classList.contains('is-fallback')")
        path_profile = cdp.shot("auth_profile.png", clip={"x": 80, "y": 0, "width": 1280, "height": 260})
        print("头像:", avatar, "| 回退首字母:", fallback)
        if not avatar or not avatar["src"].startswith("https://api.dicebear.com/9.x/initials/svg?seed=user"):
            print("FAIL 头像没有使用 DiceBear + username seed")
            ok = False

        print("截图:", path_login)
        print("截图:", path_error)
        print("截图:", path_taken)
        print("截图:", path_free)
        print("截图:", path_profile)
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
