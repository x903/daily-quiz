/* 找回密码 / 重置密码页交互（两个页面共用） */
(function () {
    'use strict';

    var EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

    function toast(message, type) {
        var stack = document.getElementById('toast-stack');
        if (!stack) return;
        var el = document.createElement('div');
        el.className = 'toast' + (type ? ' ' + type : '');
        el.textContent = message;
        stack.appendChild(el);
        setTimeout(function () {
            el.style.transition = 'opacity .2s ease, transform .2s ease';
            el.style.opacity = '0';
            el.style.transform = 'translateY(6px)';
            setTimeout(function () { el.remove(); }, 240);
        }, 3200);
    }

    async function post(url, payload) {
        var res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload || {})
        });
        var body = null;
        try { body = await res.json(); } catch (e) { body = null; }
        return { status: res.status, ok: !!(res.ok && body && body.ok), data: body };
    }

    function errorOf(res, fallback) {
        return (res.data && res.data.error) || fallback || '操作失败，请稍后重试';
    }

    var steps = Array.prototype.slice.call(document.querySelectorAll('.step'));
    function setStep(current) {
        steps.forEach(function (step) {
            var index = Number(step.getAttribute('data-step'));
            step.classList.toggle('is-active', index === current);
            step.classList.toggle('is-done', index < current);
        });
    }
    function finishSteps() {
        steps.forEach(function (step) {
            step.classList.remove('is-active');
            step.classList.add('is-done');
        });
    }

    /* ---------------- 第 1 步：提交邮箱 ---------------- */
    var forgotForm = document.getElementById('forgot-form');
    if (forgotForm) {
        var forgotSubmit = document.getElementById('forgot-submit');
        forgotForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var email = (document.getElementById('forgot-email').value || '').trim();
            if (!EMAIL_RE.test(email)) { toast('请输入正确的邮箱地址', 'err'); return; }

            forgotSubmit.disabled = true;
            forgotSubmit.textContent = '发送中…';
            var res = await post('/api/auth/forgot-password', { email: email });
            forgotSubmit.disabled = false;
            forgotSubmit.textContent = '发送重置邮件';

            if (!res.ok) { toast(errorOf(res, '发送失败'), 'err'); return; }

            var data = res.data.data;
            var echo = document.getElementById('forgot-echo');
            if (echo) echo.textContent = data.email;
            var dev = document.getElementById('forgot-dev');
            if (dev && data.debug_link && data.debug_link.indexOf('http') === 0) {
                dev.hidden = false;
                dev.textContent = '开发模式（未配置 SMTP）：';
                var link = document.createElement('a');
                link.href = data.debug_link;
                link.textContent = '打开重置链接';
                dev.appendChild(link);
            }
            document.getElementById('forgot-panel').hidden = true;
            document.getElementById('forgot-sent').hidden = false;
            setStep(2);
            toast('重置邮件已发送', 'ok');
        });

        var again = document.getElementById('forgot-again');
        if (again) again.addEventListener('click', function () {
            document.getElementById('forgot-sent').hidden = true;
            document.getElementById('forgot-panel').hidden = false;
            setStep(1);
        });
    }

    /* ---------------- 第 3 步：设置新密码 ---------------- */
    var resetForm = document.getElementById('reset-form');
    if (resetForm) {
        var passwordInput = document.getElementById('reset-password');
        var confirmInput = document.getElementById('reset-confirm');
        var bar = document.getElementById('pw-strength-bar');
        var label = document.getElementById('pw-strength-text');

        function strengthOf(pw) {
            if (!pw) return 0;
            var score = 0;
            if (pw.length >= 8) score += 1;
            if (pw.length >= 12) score += 1;
            if (/[A-Za-z]/.test(pw) && /\d/.test(pw)) score += 1;
            if (/[^A-Za-z0-9]/.test(pw)) score += 1;
            return Math.min(score, 4);
        }

        var META = [
            { width: '0%', color: '#e5eaf2', text: '8-16 位，需同时包含字母和数字' },
            { width: '25%', color: '#e05252', text: '强度：弱' },
            { width: '50%', color: '#e6a23c', text: '强度：中' },
            { width: '75%', color: '#3b6ef5', text: '强度：较强' },
            { width: '100%', color: '#2f9e5f', text: '强度：强' }
        ];

        passwordInput.addEventListener('input', function () {
            var meta = META[strengthOf(passwordInput.value)];
            bar.style.width = meta.width;
            bar.style.background = meta.color;
            label.textContent = meta.text;
            label.classList.remove('is-error', 'is-ok');
        });

        resetForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var pw = passwordInput.value;
            var confirm = confirmInput.value;
            var token = document.getElementById('reset-token').value;
            var submit = document.getElementById('reset-submit');

            if (pw.length < 8 || pw.length > 16) { toast('密码长度需为 8-16 位', 'err'); return; }
            if (!/[A-Za-z]/.test(pw) || !/\d/.test(pw)) {
                toast('密码需同时包含字母和数字', 'err'); return;
            }
            if (pw !== confirm) { toast('两次输入的新密码不一致', 'err'); return; }

            submit.disabled = true;
            submit.textContent = '提交中…';
            var res = await post('/api/auth/reset-password', {
                token: token, password: pw, confirm_password: confirm
            });
            submit.disabled = false;
            submit.textContent = '确认重置';

            if (!res.ok) { toast(errorOf(res, '重置失败'), 'err'); return; }
            document.getElementById('reset-panel').hidden = true;
            document.getElementById('reset-done').hidden = false;
            finishSteps();
            toast('密码已重置，请使用新密码登录', 'ok');
        });
    }
})();
