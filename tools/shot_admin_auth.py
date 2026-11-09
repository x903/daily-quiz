"""截图核对：管理员登录验证码 + 管理后台改密 / 重置密码入口。

前置：站点已启动（默认 http://127.0.0.1:5000）、已安装 websocket-client、
MySQL / Redis 容器可 exec。
运行：python tools/shot_admin_auth.py
输出：系统临时目录 daily_quiz_shots/ 下的 admin_*.png 与 RESULT: PASS/FAIL。

脚本会用一次性探针管理员（shot_probe_admin）走完整改密流程，
结束后删除该账号，不会改动真实 admin 的密码。
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
ADMIN_PW = os.environ.get("ADMIN_PASSWORD") or "admin123"
PORT = 9351
OUT_DIR = os.path.join(tempfile.gettempdir(), "daily_quiz_shots")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE_NAME = "shot_probe_admin"
PROBE_OLD = "shotProbe123"
PROBE_NEW = "shotProbe456"


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

    def goto(self, url, wait=1.5):
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


def compose(*args):
    return subprocess.run(["docker", "compose", *args], cwd=PROJECT,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)


def redis_get(key):
    out = compose("exec", "-T", "redis", "redis-cli", "--raw", "get", key).stdout
    return out.decode("utf-8").strip()


def clear_rate_limits():
    """截图会反复取码，先清掉管理端取码计数，避免脚本被自己的限流拦住。"""
    compose("exec", "-T", "redis", "sh", "-c",
            "redis-cli --scan --pattern 'admin:login:captcha:rate:*' | xargs -r redis-cli del")


def drop_probe():
    compose("exec", "-T", "db", "mysql", "-uquiz", "-pquizpass", "-e",
            "DELETE FROM daily_quiz.admins WHERE username='%s';" % PROBE_NAME)


def wait_for(cdp, expr, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cdp.js(expr):
            return True
        time.sleep(0.3)
    return False


def fill_and_submit(cdp, fields, form_selector):
    """按 id 填值并提交表单；密码等敏感值只在页面上下文里出现。"""
    payload = json.dumps(fields)
    return cdp.js("""(() => {
        const values = %s;
        Object.keys(values).forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = values[id];
        });
        const form = document.querySelector('%s');
        if (!form) return false;
        form.requestSubmit();
        return true;
    })()""" % (payload, form_selector))


def submit_login(cdp, username, password):
    """等页面拿到验证码 key，从 Redis 取明文，填表并提交。"""
    key = ""
    end = time.time() + 8
    while time.time() < end and not key:
        key = cdp.js("(document.getElementById('captcha-key') || {}).value || ''")
        if not key:
            time.sleep(0.3)
    if not key:
        return False
    code = redis_get("admin:login:captcha:" + key)
    return fill_and_submit(cdp, {"username": username, "password": password, "captcha": code},
                           "#login-form")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    clear_rate_limits()
    drop_probe()
    profile_dir = tempfile.mkdtemp(prefix="cdp_admin_auth_")
    proc = subprocess.Popen(
        [
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--remote-debugging-port=%d" % PORT,
            "--user-data-dir=%s" % profile_dir,
            "--window-size=1440,1000", "about:blank",
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
                 width=1440, height=1000, deviceScaleFactor=1, mobile=False)

        # ---- 登录页：验证码图片由接口异步载入
        cdp.goto(BASE + "/admin/login", wait=2.6)
        state = cdp.js("""(() => {
            const img = document.getElementById('captcha-img');
            const key = document.getElementById('captcha-key');
            return {
                image: !!img && img.src.startsWith('data:image/svg+xml;base64,'),
                key: key ? key.value : '',
                hint: (document.getElementById('captcha-hint') || {}).textContent || ''
            };
        })()""")
        print("登录页验证码:", state)
        path_login = cdp.shot("admin_login.png", clip={"x": 380, "y": 0, "width": 680, "height": 800})
        if not state["image"] or not state["key"]:
            print("FAIL 管理登录页验证码未渲染或未拿到 key")
            ok = False

        # ---- 错误验证码：提示 + 自动换码
        old_key = state["key"]
        fill_and_submit(cdp, {"username": "admin", "password": ADMIN_PW, "captcha": "ZZZZ"},
                        "#login-form")
        time.sleep(2.2)
        err_text = cdp.js("(document.getElementById('login-error') || {}).textContent || ''")
        new_key = cdp.js("document.getElementById('captcha-key').value || ''")
        path_bad = cdp.shot("admin_login_error.png", clip={"x": 380, "y": 0, "width": 680, "height": 800})
        print("错误提示:", err_text, "| 已换码:", old_key != new_key)
        if "验证码" not in (err_text or ""):
            print("FAIL 验证码错误提示缺失")
            ok = False
        if old_key == new_key:
            print("FAIL 登录失败后没有刷新验证码")
            ok = False

        # ---- 正确验证码登录（验证码从 Redis 取，等价于用户看图）
        if not submit_login(cdp, "admin", ADMIN_PW):
            print("FAIL 登录页未取得验证码 key")
            ok = False
        time.sleep(2.8)
        url = cdp.js("location.pathname + location.hash")
        print("登录后地址:", url)
        if not url.startswith("/admin/dashboard"):
            print("FAIL 正确验证码未能进入管理后台")
            ok = False

        # ---- 先建探针管理员，让「用户与权限」出现重置密码入口
        created = cdp.js("""fetch('/api/admin/admins', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({username: '%s', password: '%s'})
        }).then(r => r.json())""" % (PROBE_NAME, PROBE_OLD))
        print("创建探针管理员:", created)

        cdp.js("location.hash = 'users'")
        wait_for(cdp, "!!document.getElementById('admin-create')")
        time.sleep(1.0)
        users_state = cdp.js("""(() => ({
            resetButtons: document.querySelectorAll('[data-reset]').length,
            selfTag: !!document.querySelector('.tag-self'),
            rows: document.querySelectorAll('#view table tbody tr').length,
            hasCreate: !!document.getElementById('admin-create')
        }))()""")
        path_users = cdp.shot("admin_users.png", clip={"x": 0, "y": 0, "width": 1440, "height": 1000})
        print("用户与权限:", users_state)
        if not users_state["resetButtons"] or not users_state["hasCreate"] or not users_state["selfTag"]:
            print("FAIL 管理员列表缺少重置密码入口或当前账号标记")
            ok = False

        # ---- 退出后以探针账号登录，走完整改密流程
        cdp.js("fetch('/api/admin/logout', {method: 'POST', credentials: 'same-origin'}).then(r => r.json())")
        time.sleep(1)
        cdp.goto(BASE + "/admin/login", wait=2.6)
        if not submit_login(cdp, PROBE_NAME, PROBE_OLD):
            print("FAIL 探针账号登录页未取得验证码 key")
            ok = False
        time.sleep(2.6)
        probe_url = cdp.js("location.pathname + location.hash")
        print("探针登录后地址:", probe_url)
        if not probe_url.startswith("/admin/dashboard"):
            print("FAIL 探针账号无法登录")
            ok = False

        # ---- 改密页：错误原密码 → 服务端提示
        cdp.js("location.hash = 'password'")
        if not wait_for(cdp, "!!document.getElementById('pwd-form')"):
            print("FAIL 改密页未渲染")
            ok = False
        time.sleep(0.6)
        path_pwd = cdp.shot("admin_password.png", clip={"x": 0, "y": 0, "width": 1440, "height": 900})

        fill_and_submit(cdp, {"pwd-old": "wrong-old-password", "pwd-new": PROBE_NEW,
                              "pwd-confirm": PROBE_NEW}, "#pwd-form")
        time.sleep(2.0)
        pwd_msg = cdp.js("(document.getElementById('pwd-msg') || {}).textContent || ''")
        print("错误原密码提示:", pwd_msg)
        if "原密码不正确" not in (pwd_msg or ""):
            print("FAIL 原密码错误未给出提示")
            ok = False
        path_pwd_err = cdp.shot("admin_password_error.png", clip={"x": 0, "y": 0, "width": 1440, "height": 900})

        # ---- 两次新密码不一致 → 前端拦截
        fill_and_submit(cdp, {"pwd-old": PROBE_OLD, "pwd-new": PROBE_NEW,
                              "pwd-confirm": PROBE_NEW + "9"}, "#pwd-form")
        time.sleep(0.8)
        mismatch = cdp.js("(document.getElementById('pwd-msg') || {}).textContent || ''")
        print("两次不一致提示:", mismatch)
        if "不一致" not in (mismatch or ""):
            print("FAIL 两次新密码不一致未被拦截")
            ok = False

        # ---- 正确改密：跳回登录页并提示用新密码登录
        fill_and_submit(cdp, {"pwd-old": PROBE_OLD, "pwd-new": PROBE_NEW,
                              "pwd-confirm": PROBE_NEW}, "#pwd-form")
        time.sleep(3.2)
        after = cdp.js("location.pathname + location.search")
        note = cdp.js("(document.getElementById('login-note') || {}).textContent || ''")
        note_visible = cdp.js("!!(document.getElementById('login-note') && !document.getElementById('login-note').hidden)")
        print("改密后地址:", after, "| 提示:", note, "| 可见:", note_visible)
        path_done = cdp.shot("admin_password_done.png", clip={"x": 380, "y": 0, "width": 680, "height": 800})
        if not after.startswith("/admin/login") or "pwd=1" not in after:
            print("FAIL 改密后没有跳转登录页")
            ok = False
        if not note_visible or "密码已修改" not in (note or ""):
            print("FAIL 改密后缺少重新登录提示")
            ok = False

        # ---- 新密码可登录
        if not submit_login(cdp, PROBE_NAME, PROBE_NEW):
            print("FAIL 改密后登录页未取得验证码 key")
            ok = False
        time.sleep(2.8)
        final_url = cdp.js("location.pathname")
        print("新密码登录后地址:", final_url)
        if final_url != "/admin/dashboard":
            print("FAIL 新密码无法登录")
            ok = False

        print("截图:", path_login)
        print("截图:", path_bad)
        print("截图:", path_users)
        print("截图:", path_pwd)
        print("截图:", path_pwd_err)
        print("截图:", path_done)
        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        drop_probe()


if __name__ == "__main__":
    sys.exit(main())
