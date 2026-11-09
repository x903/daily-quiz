"""截图核对：题库目录 → 模拟答题 → 交卷解析 → 管理端题库管理。

前置：站点已启动（默认 http://127.0.0.1:5000）、Docker 容器可 exec、
已安装 websocket-client、已执行 seed_practice.py。
运行：python tools/shot_practice.py
输出：系统临时目录 daily_quiz_shots/ 下的 practice_*.png / admin_practice*.png
与 RESULT: PASS/FAIL。

脚本使用一次性探针用户 shot_practice_user，结束后级联删除该用户的
练习场次、作答与进度，不会污染真实账号。
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
PORT = 9352
OUT_DIR = os.path.join(tempfile.gettempdir(), "daily_quiz_shots")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE_USER = "shot_practice_user"
PROBE_PASS = "shotPractice123"


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
        self.ws = websocket.create_connection(ws_url, timeout=30)
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
        result = r.get("result", {})
        if result.get("subtype") == "error":
            raise RuntimeError(result.get("description") or "JS 执行失败")
        return result.get("value")

    def goto(self, url, wait=1.5):
        self.send("Page.navigate", url=url)
        time.sleep(wait)

    def shot(self, name, clip=None, full=False):
        params = {"format": "png", "captureBeyondViewport": False}
        if full:
            metrics = self.send("Page.getLayoutMetrics")
            size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            width = int(size.get("width") or 1440)
            height = min(int(size.get("height") or 1000), 3600)
            params["captureBeyondViewport"] = True
            params["clip"] = {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}
        elif clip:
            params["clip"] = dict(clip, scale=1)
        data = self.send("Page.captureScreenshot", **params)["data"]
        path = os.path.join(OUT_DIR, name)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path


def compose(*args):
    return subprocess.run(["docker", "compose", *args], cwd=PROJECT,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def redis_get(key):
    out = compose("exec", "-T", "redis", "redis-cli", "--raw", "get", key).stdout
    return out.decode("utf-8").strip()


def clear_rate_limits():
    compose("exec", "-T", "redis", "sh", "-c",
            "redis-cli --scan --pattern '*login:captcha:rate:*' | xargs -r redis-cli del")


def mysql(sql):
    out = compose("exec", "-T", "db", "mysql", "-uquiz", "-pquizpass", "-N", "-B", "-e", sql).stdout
    return out.decode("utf-8", "replace")


def prepare_probe():
    mysql("DELETE FROM daily_quiz.users WHERE username='%s';" % PROBE_USER)
    code = (
        "import models, auth;"
        "models.create_user('%s', auth.hash_password('%s'), 'user');"
        "print('probe-ready')" % (PROBE_USER, PROBE_PASS)
    )
    out = compose("exec", "-T", "web", "python", "-c", code)
    if b"probe-ready" not in out.stdout:
        raise RuntimeError("探针用户创建失败：" + out.stderr.decode("utf-8", "replace")[:300])


def drop_probe():
    mysql("DELETE FROM daily_quiz.users WHERE username='%s';" % PROBE_USER)


def wait_for(cdp, expr, timeout=12.0):
    end = time.time() + timeout
    while time.time() < end:
        if cdp.js(expr):
            return True
        time.sleep(0.3)
    return False


def login(cdp, login_path, username, password, redis_prefix, next_path):
    cdp.goto(BASE + login_path, wait=2.0)
    if not wait_for(cdp, "!!(document.getElementById('captcha-key') && document.getElementById('captcha-key').value)", 8):
        return False
    key = cdp.js("document.getElementById('captcha-key').value")
    code = redis_get(redis_prefix + key)
    if not code:
        return False
    cdp.js("""(() => {
        document.getElementById('username').value = %s;
        document.getElementById('password').value = %s;
        document.getElementById('captcha').value = %s;
        document.getElementById('login-form').requestSubmit();
        return true;
    })()""" % (json.dumps(username), json.dumps(password), json.dumps(code)))
    time.sleep(2.8)
    return cdp.js("location.pathname") not in (login_path,)


def answers_for_bank(slug):
    """从 MySQL 读取该题库每题的正确答案，返回 {question_id: ['A', ...]}。"""
    raw = mysql(
        "SELECT q.id, q.answer FROM daily_quiz.bank_questions q "
        "JOIN daily_quiz.question_banks b ON b.id=q.bank_id "
        "WHERE b.slug='%s' AND q.status='active';" % slug
    )
    out = {}
    for line in raw.splitlines():
        if "\t" not in line:
            continue
        qid, answer = line.split("\t", 1)
        try:
            letters = json.loads(answer)
        except ValueError:
            letters = list(answer.strip())
        out[int(qid)] = letters
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    prepare_probe()
    clear_rate_limits()
    profile = tempfile.mkdtemp(prefix="cdp_practice_")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--remote-debugging-port=%d" % PORT, "--user-data-dir=%s" % profile,
         "--window-size=1440,1100", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = True
    try:
        page = next(t for t in wait_json("/json/list") if t["type"] == "page")
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")

        # ---------------------------------------------------------- 1) 认证页题库区块
        if not login(cdp, "/login", PROBE_USER, PROBE_PASS, "login:captcha:", "/"):
            print("FAIL 普通用户登录失败")
            return 1
        cdp.goto(BASE + "/cert/aliyun/acp/cloud", wait=2.2)
        cards = cdp.js("document.querySelectorAll('.bank-grid-cert .bank-card').length")
        print("认证页题库卡片数:", cards)
        if cards < 3:
            print("FAIL 认证页题库卡片不足")
            ok = False
        print("区块统计:", cdp.js("(document.querySelector('#bank-list .section-head p')||{}).textContent.trim()"))
        path_list = cdp.shot("cert_banks_list.png", full=True)
        print("截图:", path_list)

        # ---------------------------------------------------------- 2) 进入 ECS 专项
        cdp.js("""(() => {
            const card = Array.from(document.querySelectorAll('.bank-grid-cert .bank-card')).find(
                c => (c.querySelector('.bank-name') || {}).textContent.indexOf('ECS') >= 0);
            card.querySelector('.bank-enter').click();
            return true;
        })()""")
        if not wait_for(cdp, "!!(document.getElementById('exam-layout') && !document.getElementById('exam-layout').hidden && (document.getElementById('q-stem')||{}).textContent)", 15):
            print("FAIL 模拟答题页未加载")
            return 1
        token = cdp.js("new URLSearchParams(location.search).get('session')")
        print("练习场次:", token)
        if not token:
            print("FAIL 未生成练习场次")
            ok = False
        stem = cdp.js("document.getElementById('q-stem').textContent")
        print("当前题干:", stem[:40])
        if not stem or "题库" in stem[:6]:
            print("FAIL 题面为空")
            ok = False
        path_quiz = cdp.shot("practice_quiz.png")
        print("截图:", path_quiz)

        # ---------------------------------------------------------- 3) 全部作答（故意错 1 题）
        answer_map = answers_for_bank("ecs-essentials")
        result = cdp.js("""(async () => {
            const map = %s;
            const token = new URLSearchParams(location.search).get('session');
            const res = await fetch('/api/practice/sessions/' + token, {credentials: 'same-origin'});
            const body = await res.json();
            const questions = body.data.questions;
            let wrongPicked = false;
            for (let i = 0; i < questions.length; i++) {
                const btn = document.querySelector('[data-jump="' + i + '"]');
                if (btn) btn.click();
                await new Promise(r => setTimeout(r, 90));
                const q = questions[i];
                const correct = map[q.id] || [];
                let picks = correct;
                if (i === 0) {
                    const bad = Object.keys(q.options).find(l => correct.indexOf(l) < 0);
                    if (bad) { picks = [bad]; wrongPicked = true; }
                }
                for (const letter of picks) {
                    const input = document.querySelector('.bank-option[data-letter="' + letter + '"] input');
                    if (input && !input.checked) input.click();
                    await new Promise(r => setTimeout(r, 30));
                }
                await new Promise(r => setTimeout(r, 80));
            }
            // 停在已作答的题上，便于截图展示答题卡
            const stop = document.querySelector('[data-jump="5"]');
            if (stop) stop.click();
            await new Promise(r => setTimeout(r, 500));
            return {total: questions.length, wrongPicked: wrongPicked,
                    answered: (document.getElementById('sheet-progress')||{}).textContent};
        })()""" % json.dumps(answer_map, ensure_ascii=False))
        print("自动作答:", result)
        if not result or result.get("total") != 30 or not result.get("wrongPicked"):
            print("FAIL 自动作答未完成")
            ok = False
        time.sleep(0.8)
        path_answered = cdp.shot("practice_answered.png")
        print("截图:", path_answered)

        # ---------------------------------------------------------- 4) 交卷
        cdp.js("document.getElementById('q-submit').click(); true")
        if not wait_for(cdp, "!!(document.getElementById('submit-confirm') && !document.getElementById('submit-confirm').hidden)", 5):
            print("FAIL 交卷确认弹层未出现")
            ok = False
        path_confirm = cdp.shot("practice_submit_confirm.png")
        print("截图:", path_confirm)
        cdp.js("document.getElementById('confirm-submit').click(); true")
        if not wait_for(cdp, "!!(document.getElementById('exam-result') && !document.getElementById('exam-result').hidden)", 25):
            print("FAIL 交卷后未显示成绩")
            return 1
        time.sleep(0.8)
        score = cdp.js("(document.querySelector('.bank-score-ring b')||{}).textContent")
        badge = cdp.js("(document.querySelector('.bank-result-badge')||{}).textContent")
        items = cdp.js("document.querySelectorAll('.bank-analysis-item').length")
        print("得分:", score, "| 结论:", badge, "| 解析题数:", items)
        if score != "29" or badge != "合格" or items != 30:
            print("FAIL 判分或解析数量不符（期望 29 分 / 合格 / 30 题）")
            ok = False
        # 交卷后答题区与倒计时头部必须真正隐藏（.bank-exam-layout 的 display:grid 会压过 [hidden]）
        layout_display = cdp.js("getComputedStyle(document.getElementById('exam-layout')).display")
        head_hidden = cdp.js("document.getElementById('exam-head').hidden")
        print("成绩页答题区 display:", layout_display, "| 头部隐藏:", head_hidden)
        if layout_display != "none" or not head_hidden:
            print("FAIL 交卷后答题区或答题页头部未隐藏")
            ok = False
        path_result = cdp.shot("practice_result.png", full=True)
        print("截图:", path_result)

        # 只看错题
        cdp.js("document.getElementById('result-wrong').click(); true")
        time.sleep(0.6)
        visible = cdp.js("Array.from(document.querySelectorAll('.bank-analysis-item')).filter(x=>!x.hidden).length")
        print("只看错题后可见:", visible)
        if visible != 1:
            print("FAIL 错题筛选应只剩 1 题")
            ok = False
        path_wrong = cdp.shot("practice_analysis_wrong.png")
        print("截图:", path_wrong)

        # ---------------------------------------------------------- 5) 回认证页看进度
        cdp.goto(BASE + "/cert/aliyun/acp/cloud", wait=2.2)
        done_text = cdp.js("""(() => {
            const card = Array.from(document.querySelectorAll('.bank-grid-cert .bank-card')).find(
                c => (c.querySelector('.bank-name') || {}).textContent.indexOf('ECS') >= 0);
            return card ? card.textContent.replace(/\\s+/g, ' ') : '';
        })()""")
        print("ECS 卡片:", done_text[:160])
        if "已练 30/30" not in done_text or "继续练习" not in done_text:
            print("FAIL 认证页卡片未显示「已练 30/30 / 继续练习」")
            ok = False
        path_done = cdp.shot("cert_banks_done.png", full=True)
        print("截图:", path_done)

        # ---------------------------------------------------------- 6) 管理端
        if not login(cdp, "/admin/login", "admin", ADMIN_PW, "admin:login:captcha:", "/admin/dashboard"):
            print("FAIL 管理员登录失败")
            return 1
        cdp.goto(BASE + "/admin/dashboard#practice", wait=2.8)
        if not wait_for(cdp, "document.querySelectorAll('#view table tbody tr').length > 0", 12):
            print("FAIL 管理端题库列表未加载")
            ok = False
        rows = cdp.js("document.querySelectorAll('#view table tbody tr').length")
        print("管理端题库行数:", rows)
        path_admin = cdp.shot("admin_practice.png", full=True)
        print("截图:", path_admin)

        # 打开编辑弹层，核对规则字段被锁定
        cdp.js("""(() => {
            const btn = document.querySelector('[data-pb-edit]');
            if (btn) btn.click();
            return true;
        })()""")
        if wait_for(cdp, "!!document.getElementById('pb-form')", 8):
            locked = cdp.js("document.getElementById('pbf-total').readOnly")
            rule = cdp.js("document.getElementById('pbf-rule').value")
            print("编辑弹层规则:", rule, "| 题量锁定:", locked)
            if not locked:
                print("FAIL 关联规则时题量未锁定")
                ok = False
            path_editor = cdp.shot("admin_practice_editor.png", clip={"x": 260, "y": 0, "width": 1160, "height": 1000})
            print("截图:", path_editor)
        else:
            print("FAIL 编辑弹层未打开")
            ok = False

        # 打开考试规则弹层：题量、比例、总分、合格分、时长均可改
        cdp.js("document.getElementById('modal').hidden = true; true")
        cdp.js("document.getElementById('pb-rules').click(); true")
        if wait_for(cdp, "!!document.querySelector('.rule-card')", 8):
            rule_count = cdp.js("document.querySelectorAll('.rule-card').length")
            print("考试规则条数:", rule_count)
            path_rules = cdp.shot("admin_exam_rules.png", clip={"x": 260, "y": 0, "width": 1160, "height": 1000})
            print("截图:", path_rules)
        else:
            print("FAIL 考试规则弹层未打开")
            ok = False

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
