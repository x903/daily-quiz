"""用 CDP 驱动真实 Chrome，模拟鼠标从一级菜单移到二级菜单，验证下拉菜单不再闪退。

前置：站点已启动（默认 http://localhost:5000），且已安装 websocket-client：
    python -m pip install websocket-client
运行：
    python tools/nav_hover_check.py
输出 RESULT: PASS / FAIL，并在系统临时目录留下 nav_hover_cdp.png 截图。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
URL = "http://localhost:5000/"
PORT = 9333


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
        self.ws = websocket.create_connection(ws_url, timeout=15)
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
        r = self.send(
            "Runtime.evaluate",
            expression=expr,
            returnByValue=True,
            awaitPromise=True,
        )
        return r.get("result", {}).get("value")


def mouse(cdp, x, y):
    cdp.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, buttons=0, pointerType="mouse")


def main():
    profile = tempfile.mkdtemp(prefix="cdp_nav_")
    proc = subprocess.Popen(
        [
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--remote-debugging-port=%d" % PORT,
            "--user-data-dir=" + profile,
            "--window-size=1440,900",
            URL,
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_json("/json/version")
        targets = wait_json("/json")
        page = [t for t in targets if t["type"] == "page"][0]
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        time.sleep(1.5)

        rects = cdp.js(
            """(function(){
              var item = document.querySelector('[data-nav-dropdown]');
              var label = item.querySelector('.nav-link');
              var panel = item.querySelector('.nav-level-2');
              var lr = label.getBoundingClientRect();
              var pr = panel.getBoundingClientRect();
              return {lx: lr.left + lr.width/2, ly: lr.top + lr.height/2,
                      lb: lr.bottom,
                      px: pr.left + pr.width/2, py: pr.top + 16,
                      ptop: pr.top, pbot: pr.bottom};
            })()"""
        )
        print("一级标签中心 (%.1f, %.1f)，标签底边 y=%.1f，二级面板 y=%.1f~%.1f"
              % (rects["lx"], rects["ly"], rects["lb"], rects["ptop"], rects["pbot"]))

        # 1) 先移到一级菜单上，等菜单展开后再量面板位置（隐藏时 rect 恒为 0）
        mouse(cdp, rects["lx"], rects["ly"])
        time.sleep(0.45)
        display = cdp.js(
            "getComputedStyle(document.querySelector('[data-nav-dropdown] .nav-level-2')).display")
        opened = cdp.js(
            """(function(){
              var p = document.querySelector('[data-nav-dropdown] .nav-level-2');
              var r = p.getBoundingClientRect();
              return {top: r.top, bottom: r.bottom};
            })()"""
        )
        print("悬停一级菜单后：面板 display = %s，面板 y=%.1f~%.1f，与标签底边相距 %.1fpx"
              % (display, opened["top"], opened["bottom"], opened["top"] - rects["lb"]))

        # 2) 沿直线逐像素下移，每步检查面板是否还在
        x0, y0 = rects["lx"], rects["lb"] - 2
        y1 = opened["top"] + 20
        steps = 26
        died_at = None
        trace = []
        for i in range(steps + 1):
            y = y0 + (y1 - y0) * i / steps
            mouse(cdp, x0, y)
            time.sleep(0.02)
            d = cdp.js(
                "getComputedStyle(document.querySelector('[data-nav-dropdown] .nav-level-2')).display")
            trace.append("%.1f:%s" % (y, d))
            if d == "none" and died_at is None:
                died_at = y
        print("下移轨迹：", " ".join(trace[:6]), "...", " ".join(trace[-4:]))
        print("面板在穿越过程中消失的位置：", died_at if died_at else "无（全程保持展开）")

        # 3) 停在二级菜单第一项上，确认可以选中并高亮
        first_item = cdp.js(
            """(function(){
              var a = document.querySelector('[data-nav-dropdown] .nav-level-2 li a');
              var r = a.getBoundingClientRect();
              return {x: r.left + r.width/2, y: r.top + r.height/2, text: a.textContent.trim(), href: a.href};
            })()"""
        )
        mouse(cdp, first_item["x"], first_item["y"])
        time.sleep(0.25)
        hov = cdp.js(
            """(function(){
              var a = document.elementFromPoint(%f, %f);
              return {tag: a ? a.tagName : null, text: a ? (a.textContent||'').trim() : null,
                      display: getComputedStyle(document.querySelector('[data-nav-dropdown] .nav-level-2')).display};
            })()""" % (first_item["x"], first_item["y"])
        )
        print("鼠标停在二级菜单首项：", hov)

        # 4) 继续横移到三级菜单
        sub = cdp.js(
            """(function(){
              var li = document.querySelector('[data-nav-dropdown] .nav-level-2 li.has-children');
              var r = li.getBoundingClientRect();
              return {x: r.right - 6, y: r.top + r.height/2};
            })()"""
        )
        mouse(cdp, sub["x"], sub["y"])
        time.sleep(0.35)
        lvl3 = cdp.js(
            """(function(){
              var p = document.querySelector('[data-nav-dropdown] .nav-level-3');
              var r = p.getBoundingClientRect();
              return {display: getComputedStyle(p).display, x: r.left + r.width/2, y: r.top + 18};
            })()"""
        )
        print("二级 => 三级：", {"display": lvl3["display"], "pos": (round(lvl3["x"]), round(lvl3["y"]))})
        mouse(cdp, lvl3["x"], lvl3["y"])
        time.sleep(0.3)
        hovered3 = cdp.js(
            "(document.elementFromPoint(%d, %d)||{}).textContent || ''" % (round(lvl3["x"]), round(lvl3["y"])))
        still = cdp.js(
            "getComputedStyle(document.querySelector('[data-nav-dropdown] .nav-level-3')).display")
        print("鼠标进入三级菜单：命中「%s」，三级 display=%s" % (hovered3.strip()[:20], still))

        # 5) 截图取证
        shot = cdp.send("Page.captureScreenshot", format="png")
        out = os.path.join(tempfile.gettempdir(), "nav_hover_cdp.png")
        with open(out, "wb") as f:
            f.write(__import__("base64").b64decode(shot["data"]))
        print("screenshot:", out)

        # 6) 移开：延迟关闭仍生效
        mouse(cdp, 200, 700)
        time.sleep(0.05)
        d0 = cdp.js("getComputedStyle(document.querySelector('[data-nav-dropdown] .nav-level-2')).display")
        time.sleep(0.5)
        d1 = cdp.js("getComputedStyle(document.querySelector('[data-nav-dropdown] .nav-level-2')).display")
        print("移开 50ms 后 display=%s，移开 550ms 后 display=%s（期望 block -> none）" % (d0, d1))

        ok = died_at is None and display == "block" and d1 == "none" and lvl3["display"] == "block"
        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
