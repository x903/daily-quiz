/* 个人中心交互：资料编辑、修改密码、绑定手机号 / 邮箱 */
(function () {
    'use strict';

    var PHONE_RE = /^1[3-9]\d{9}$/;
    var EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

    /* ---------------- 通用 ---------------- */
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

    async function api(method, url, payload) {
        var res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: payload ? JSON.stringify(payload) : null
        });
        var body = null;
        try { body = await res.json(); } catch (e) { body = null; }
        return { status: res.status, ok: !!(res.ok && body && body.ok), data: body };
    }

    function errorOf(res, fallback) {
        return (res.data && res.data.error) || fallback || '操作失败，请稍后重试';
    }

    function setHint(el, text, kind) {
        if (!el) return;
        el.textContent = text;
        el.classList.remove('is-error', 'is-ok');
        if (kind) el.classList.add('is-' + kind);
    }

    function lock(button, text) {
        if (!button) return;
        button.dataset.label = button.textContent;
        button.disabled = true;
        if (text) button.textContent = text;
    }

    function unlock(button) {
        if (!button) return;
        button.disabled = false;
        if (button.dataset.label) button.textContent = button.dataset.label;
    }

    /* ---------------- 弹窗 ---------------- */
    function openModal(id) {
        var layer = document.getElementById(id);
        if (!layer) return;
        layer.hidden = false;
        document.body.classList.add('modal-open');
        var first = layer.querySelector('input:not([type=hidden]), textarea');
        if (first) setTimeout(function () { first.focus(); }, 60);
    }

    function closeModal(id) {
        var layer = document.getElementById(id);
        if (layer) layer.hidden = true;
        if (!document.querySelector('.modal-layer:not([hidden])')) {
            document.body.classList.remove('modal-open');
        }
    }

    document.querySelectorAll('[data-open]').forEach(function (btn) {
        btn.addEventListener('click', function () { openModal(btn.getAttribute('data-open')); });
    });
    document.querySelectorAll('[data-close]').forEach(function (btn) {
        btn.addEventListener('click', function () { closeModal(btn.getAttribute('data-close')); });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        var open = document.querySelector('.modal-layer:not([hidden])');
        if (open) closeModal(open.id);
    });

    /* ---------------- 基础信息 ---------------- */
    var basicView = document.getElementById('basic-view');
    var basicForm = document.getElementById('basic-form');
    var basicEdit = document.getElementById('basic-edit');
    var basicCancel = document.getElementById('basic-cancel');
    var basicSave = document.getElementById('basic-save');
    var bioInput = document.getElementById('basic-bio');
    var bioCount = document.getElementById('bio-count');

    function enterEdit() {
        if (!basicForm) return;
        basicForm.hidden = false;
        basicView.hidden = true;
        basicEdit.hidden = true;
    }

    function exitEdit() {
        basicForm.hidden = true;
        basicView.hidden = false;
        basicEdit.hidden = false;
    }

    if (basicEdit) {
        basicEdit.addEventListener('click', enterEdit);
        basicCancel.addEventListener('click', exitEdit);
    }
    if (bioInput) {
        bioInput.addEventListener('input', function () {
            bioCount.textContent = String(bioInput.value.length);
            if (bioInput.value.length >= 200) bioCount.parentNode.classList.add('is-error');
            else bioCount.parentNode.classList.remove('is-error');
        });
    }

    if (basicForm) {
        basicForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var nickname = document.getElementById('basic-nickname').value.trim();
            var checked = basicForm.querySelector('input[name=gender]:checked');
            var gender = checked ? checked.value : '保密';
            var bio = bioInput.value.trim();

            if (nickname.length < 2 || nickname.length > 20) {
                toast('昵称需为 2-20 个字符', 'err');
                return;
            }
            if (bio.length > 200) {
                toast('个人简介最多 200 字', 'err');
                return;
            }

            lock(basicSave, '保存中…');
            var res = await api('PUT', '/api/user/profile', {
                nickname: nickname, gender: gender, bio: bio
            });
            unlock(basicSave);
            if (!res.ok) { toast(errorOf(res, '保存失败'), 'err'); return; }

            var data = res.data.data;
            var nameEl = document.querySelector('[data-basic=nickname]');
            var genderEl = document.querySelector('[data-basic=gender]');
            var bioEl = document.querySelector('[data-basic=bio]');
            if (nameEl) nameEl.textContent = data.nickname;
            if (genderEl) genderEl.textContent = data.gender;
            if (bioEl) {
                bioEl.textContent = data.bio || '还没有填写个人简介，写一句备考目标吧。';
                bioEl.classList.toggle('is-empty', !data.bio);
            }
            var heroName = document.getElementById('hero-name');
            var heroAvatar = document.getElementById('hero-avatar');
            if (heroName) heroName.textContent = data.nickname;
            if (heroAvatar) heroAvatar.textContent = (Array.from(data.nickname || '?')[0] || '?').toUpperCase();

            var raw = document.getElementById('basic-nickname');
            if (raw) raw.value = data.nickname_editable || data.nickname;
            bioCount.textContent = String((data.bio || '').length);
            bioCount.parentNode.classList.remove('is-error');
            exitEdit();
            toast('基础信息已更新', 'ok');
        });
    }

    /* ---------------- 修改密码 ---------------- */
    var pwForm = document.getElementById('password-form');
    var pwSubmit = document.getElementById('pw-submit');

    if (pwForm) {
        pwForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var oldPw = document.getElementById('pw-old').value;
            var newPw = document.getElementById('pw-new').value;
            var confirmPw = document.getElementById('pw-confirm').value;

            if (!oldPw) { toast('请输入原密码', 'err'); return; }
            if (newPw.length < 8 || newPw.length > 16) {
                toast('新密码长度需为 8-16 位', 'err'); return;
            }
            if (!/[A-Za-z]/.test(newPw) || !/\d/.test(newPw)) {
                toast('新密码需同时包含字母和数字', 'err'); return;
            }
            if (newPw !== confirmPw) { toast('两次输入的新密码不一致', 'err'); return; }
            if (newPw === oldPw) { toast('新密码不能与原密码相同', 'err'); return; }

            lock(pwSubmit, '提交中…');
            var res = await api('PUT', '/api/user/password', {
                old_password: oldPw, new_password: newPw, confirm_password: confirmPw
            });
            if (!res.ok) {
                unlock(pwSubmit);
                toast(errorOf(res, '修改失败'), 'err');
                return;
            }
            pwForm.reset();
            toast('密码修改成功，请使用新密码重新登录', 'ok');
            setTimeout(function () { window.location.href = '/login?next=%2Fprofile'; }, 1800);
        });
    }

    /* ---------------- 验证码倒计时 ---------------- */
    function startCountdown(button, seconds) {
        if (!button) return;
        if (button._timer) clearInterval(button._timer);
        var left = seconds;
        button.disabled = true;
        button.textContent = left + 's 后重发';
        button._timer = setInterval(function () {
            left -= 1;
            if (left <= 0) {
                clearInterval(button._timer);
                button._timer = null;
                button.disabled = false;
                button.textContent = '重新获取';
                return;
            }
            button.textContent = left + 's 后重发';
        }, 1000);
    }

    function showDevCode(hintEl, code) {
        if (!hintEl) return;
        hintEl.innerHTML = '开发模式验证码：<b>' + code + '</b>（未配置网关，正式环境不会显示）';
    }

    /* ---------------- 绑定手机号 ---------------- */
    var phoneForm = document.getElementById('phone-form');
    var phoneInput = document.getElementById('phone-input');
    var phoneCode = document.getElementById('phone-code');
    var phoneSend = document.getElementById('phone-send');
    var phoneHint = document.getElementById('phone-hint');
    var phoneSubmit = document.getElementById('phone-submit');

    function syncSecurity(kind, masked) {
        var valueEl = document.querySelector('[data-sec=' + kind + ']');
        var tagEl = document.querySelector('[data-sec-tag=' + kind + ']');
        var openBtn = document.getElementById(kind + '-open');
        if (valueEl) valueEl.textContent = masked;
        if (tagEl) {
            tagEl.textContent = '已验证';
            tagEl.className = 'tag tag-ok';
        }
        if (openBtn) openBtn.textContent = '更换';
    }

    if (phoneSend) {
        phoneSend.addEventListener('click', async function () {
            var phone = (phoneInput.value || '').trim();
            if (!PHONE_RE.test(phone)) { toast('请输入正确的 11 位手机号', 'err'); return; }
            lock(phoneSend, '发送中…');
            var res = await api('POST', '/api/user/bind-phone', { phone: phone });
            if (!res.ok) {
                unlock(phoneSend);
                toast(errorOf(res, '验证码发送失败'), 'err');
                return;
            }
            var data = res.data.data;
            startCountdown(phoneSend, data.cooldown || 60);
            if (data.debug_code) {
                showDevCode(phoneHint, data.debug_code);
                phoneCode.value = data.debug_code;
                phoneHint.classList.add('is-ok');
            } else {
                setHint(phoneHint, '验证码已发送，10 分钟内有效。', 'ok');
            }
            toast('验证码已发送', 'ok');
        });
    }

    if (phoneForm) {
        phoneForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var phone = (phoneInput.value || '').trim();
            var code = (phoneCode.value || '').trim();
            if (!PHONE_RE.test(phone)) { toast('请输入正确的 11 位手机号', 'err'); return; }
            if (!/^\d{6}$/.test(code)) { toast('请输入 6 位短信验证码', 'err'); return; }

            lock(phoneSubmit, '绑定中…');
            var res = await api('POST', '/api/user/verify-phone', { phone: phone, code: code });
            unlock(phoneSubmit);
            if (!res.ok) { toast(errorOf(res, '绑定失败'), 'err'); return; }

            syncSecurity('phone', res.data.data.phone);
            phoneForm.reset();
            setHint(phoneHint, '验证码 10 分钟内有效，仅用于本次绑定。');
            closeModal('modal-phone');
            toast(res.data.data.message || '手机号绑定成功', 'ok');
        });
    }

    /* ---------------- 绑定邮箱 ---------------- */
    var emailForm = document.getElementById('email-form');
    var emailInput = document.getElementById('email-input');
    var emailCode = document.getElementById('email-code');
    var emailSend = document.getElementById('email-send');
    var emailHint = document.getElementById('email-hint');
    var emailSubmit = document.getElementById('email-submit');

    if (emailSend) {
        emailSend.addEventListener('click', async function () {
            var email = (emailInput.value || '').trim();
            if (!EMAIL_RE.test(email)) { toast('请输入正确的邮箱地址', 'err'); return; }
            lock(emailSend, '发送中…');
            var res = await api('POST', '/api/user/bind-email', { email: email });
            if (!res.ok) {
                unlock(emailSend);
                toast(errorOf(res, '验证码发送失败'), 'err');
                return;
            }
            var data = res.data.data;
            startCountdown(emailSend, data.cooldown || 60);
            if (data.debug_code) {
                showDevCode(emailHint, data.debug_code);
                emailCode.value = data.debug_code;
                emailHint.classList.add('is-ok');
            } else {
                setHint(emailHint, '验证码已发送至邮箱，10 分钟内有效。', 'ok');
            }
            toast('验证码已发送', 'ok');
        });
    }

    if (emailForm) {
        emailForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            var email = (emailInput.value || '').trim();
            var code = (emailCode.value || '').trim();
            if (!EMAIL_RE.test(email)) { toast('请输入正确的邮箱地址', 'err'); return; }
            if (!/^\d{6}$/.test(code)) { toast('请输入 6 位邮箱验证码', 'err'); return; }

            lock(emailSubmit, '绑定中…');
            var res = await api('POST', '/api/user/verify-email', { email: email, code: code });
            unlock(emailSubmit);
            if (!res.ok) { toast(errorOf(res, '绑定失败'), 'err'); return; }

            syncSecurity('email', res.data.data.email);
            emailForm.reset();
            setHint(emailHint, '验证码 10 分钟内有效，验证通过后邮箱标记为已验证。');
            closeModal('modal-email');
            toast(res.data.data.message || '邮箱绑定成功', 'ok');
        });
    }
})();
