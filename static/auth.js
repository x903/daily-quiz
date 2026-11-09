/* 登录 / 注册页交互：
   - 登录：验证码加载与点击刷新、fetch 提交、失败后自动换码并保留账号密码
   - 注册：用户名失焦实时校验（绿 / 红）、提交前二次校验、按钮 loading
   无 JS 时两页仍可走原生表单提交，服务端会做同样的校验。 */
(function () {
    'use strict';

    var $ = function (sel, root) { return (root || document).querySelector(sel); };

    function setMsg(el, text, state) {
        if (!el) return;
        el.textContent = text || '';
        el.classList.remove('is-ok', 'is-err', 'is-loading');
        if (state) el.classList.add(state);
        el.hidden = !text;
    }

    function setBusy(btn, busy, text) {
        if (!btn) return;
        if (busy) {
            btn.dataset.label = btn.textContent;
            btn.textContent = text;
            btn.disabled = true;
        } else {
            if (btn.dataset.label) btn.textContent = btn.dataset.label;
            btn.disabled = false;
        }
    }

    function postJSON(url, payload) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            credentials: 'same-origin'
        }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (data) {
                return { status: resp.status, data: data };
            });
        });
    }

    function nextValue(form) {
        var input = form && form.querySelector('input[name="next"]');
        return input ? input.value : '';
    }

    /* ------------------------------------------------------------ 登录页 */
    var loginForm = $('#login-form');
    if (loginForm) {
        var captchaImg = $('#captcha-img');
        var captchaKey = $('#captcha-key');
        var captchaInput = $('#captcha');
        var refreshBtn = $('#captcha-refresh');
        var loginSubmit = $('#login-submit');
        var loginError = $('#form-error');
        var refreshing = false;

        function refreshCaptcha(focus) {
            if (refreshing) return Promise.resolve();
            refreshing = true;
            return fetch('/api/auth/captcha', { credentials: 'same-origin' })
                .then(function (resp) {
                    return resp.json().then(function (data) { return { status: resp.status, data: data }; });
                })
                .then(function (res) {
                    if (!res.data || !res.data.ok) {
                        setMsg(loginError, (res.data && res.data.error) || '验证码加载失败，请稍后重试', 'is-err');
                        return;
                    }
                    captchaImg.src = res.data.data.image;
                    captchaKey.value = res.data.data.captchaKey;
                    captchaInput.value = '';
                    if (focus) captchaInput.focus();
                })
                .catch(function () {
                    setMsg(loginError, '网络异常，验证码加载失败', 'is-err');
                })
                .then(function () { refreshing = false; });
        }

        captchaImg.addEventListener('click', function () { refreshCaptcha(true); });
        if (refreshBtn) refreshBtn.addEventListener('click', function () { refreshCaptcha(true); });

        // 失焦只做「有没有填 / 位数对不对」的轻校验，正确与否最终以后端为准
        captchaInput.addEventListener('blur', function () {
            var value = captchaInput.value.trim();
            if (!value) {
                setMsg(loginError, '请输入验证码', 'is-err');
                return;
            }
            if (!/^[A-Za-z0-9]{4,6}$/.test(value)) {
                setMsg(loginError, '验证码为 4-6 位字母或数字', 'is-err');
                return;
            }
            setMsg(loginError, '', null);
        });

        loginForm.addEventListener('submit', function (ev) {
            ev.preventDefault();
            var serverAlert = document.querySelector('.form-alert');
            if (serverAlert) serverAlert.hidden = true;
            setMsg(loginError, '', null);

            var username = $('#username').value.trim();
            var password = $('#password').value;
            var code = captchaInput.value.trim();
            if (!username || !password) {
                setMsg(loginError, '请输入用户名和密码', 'is-err');
                return;
            }
            if (!code) {
                setMsg(loginError, '请输入验证码', 'is-err');
                captchaInput.focus();
                return;
            }

            setBusy(loginSubmit, true, '登录中…');
            postJSON('/api/auth/login', {
                username: username,
                password: password,
                captchaKey: captchaKey.value,
                captchaCode: code,
                next: nextValue(loginForm)
            }).then(function (res) {
                var data = res.data || {};
                if (data.ok) {
                    var target = (data.data && data.data.redirect) || '/';
                    window.location.href = target;
                    return;
                }
                setMsg(loginError, data.error || '登录失败，请重试', 'is-err');
                // 失败后强制换一张：旧验证码已被消费，避免用户对着废码重试
                return refreshCaptcha(false).then(function () { captchaInput.focus(); });
            }).catch(function () {
                setMsg(loginError, '网络异常，请检查网络后重试', 'is-err');
            }).then(function () {
                setBusy(loginSubmit, false);
            });
        });
    }

    /* ------------------------------------------------------------ 注册页 */
    var registerForm = $('#register-form');
    if (registerForm) {
        var nameInput = $('#username');
        var nameMsg = $('#username-msg');
        var registerSubmit = $('#register-submit');
        var registerError = $('#form-error');
        var defaultHint = nameMsg ? nameMsg.textContent : '';
        var lastChecked = { value: '', available: false };

        function markUsername(state) {
            nameInput.classList.remove('is-ok', 'is-err');
            if (state) nameInput.classList.add(state);
        }

        function localValidate(value) {
            if (!value) return { ok: false, message: '请输入用户名' };
            if (value.length < 4 || value.length > 20) {
                return { ok: false, message: '用户名长度需为 4-20 位' };
            }
            if (!/^[A-Za-z0-9_.]+$/.test(value)) {
                return { ok: false, message: '用户名只能包含字母、数字、下划线或点号' };
            }
            if (/^[0-9]+$/.test(value)) return { ok: false, message: '用户名不能为纯数字' };
            if (value.charAt(0) === '.' || value.charAt(value.length - 1) === '.' || value.indexOf('..') !== -1) {
                return { ok: false, message: '用户名格式不正确，点号不能出现在首尾或连续出现' };
            }
            return { ok: true, message: '' };
        }

        function checkUsername(value, showLoading) {
            var name = (value || '').trim();
            lastChecked = { value: name, available: false };
            if (!name) {
                markUsername('');
                setMsg(nameMsg, defaultHint, null);
                return Promise.resolve(null);
            }
            var local = localValidate(name);
            if (!local.ok) {
                markUsername('is-err');
                setMsg(nameMsg, local.message, 'is-err');
                return Promise.resolve({ available: false, message: local.message });
            }

            markUsername('');
            if (showLoading !== false) setMsg(nameMsg, '正在检查用户名…', 'is-loading');

            return fetch('/api/auth/check-username?username=' + encodeURIComponent(name), {
                credentials: 'same-origin'
            }).then(function (resp) {
                return resp.json().catch(function () { return {}; }).then(function (data) {
                    return { status: resp.status, data: data };
                });
            }).then(function (res) {
                var data = res.data || {};
                if (res.status === 429) {
                    markUsername('');
                    setMsg(nameMsg, data.error || '校验过于频繁，请稍后再试', 'is-err');
                    return { available: false, message: data.error };
                }
                if (data.available) {
                    lastChecked = { value: name, available: true };
                    markUsername('is-ok');
                    setMsg(nameMsg, '该用户名可以使用', 'is-ok');
                    return { available: true, message: '该用户名可以使用' };
                }
                markUsername('is-err');
                setMsg(nameMsg, data.message || '该用户名已被注册，请更换', 'is-err');
                return { available: false, message: data.message };
            }).catch(function () {
                markUsername('');
                setMsg(nameMsg, '校验失败，请稍后再试', 'is-err');
                return null;
            });
        }

        nameInput.addEventListener('blur', function () {
            checkUsername(nameInput.value);
        });
        nameInput.addEventListener('input', function () {
            if (lastChecked.value !== nameInput.value.trim()) {
                markUsername('');
                setMsg(nameMsg, defaultHint, null);
            }
        });

        registerForm.addEventListener('submit', function (ev) {
            ev.preventDefault();
            var serverAlert = document.querySelector('.form-alert');
            if (serverAlert) serverAlert.hidden = true;
            setMsg(registerError, '', null);

            var username = nameInput.value.trim();
            var password = $('#password').value;
            var confirm = $('#confirm').value;
            var email = $('#email').value.trim();
            var phone = $('#phone').value.trim();

            var local = localValidate(username);
            if (!local.ok) {
                markUsername('is-err');
                setMsg(nameMsg, local.message, 'is-err');
                nameInput.focus();
                return;
            }
            if (password.length < 6) {
                setMsg(registerError, '密码长度至少 6 位', 'is-err');
                return;
            }
            if (password !== confirm) {
                setMsg(registerError, '两次输入的密码不一致', 'is-err');
                return;
            }

            setBusy(registerSubmit, true, '注册中…');
            var check = lastChecked.value === username && lastChecked.available
                ? Promise.resolve({ available: true })
                : checkUsername(username, true);

            check.then(function (result) {
                if (!result || !result.available) {
                    setMsg(registerError, (result && result.message) || '用户名不可用，请更换', 'is-err');
                    return null;
                }
                return postJSON('/api/auth/register', {
                    username: username,
                    password: password,
                    confirmPassword: confirm,
                    email: email,
                    phone: phone,
                    next: nextValue(registerForm)
                }).then(function (res) {
                    var data = res.data || {};
                    if (data.ok) {
                        window.location.href = (data.data && data.data.redirect) || '/';
                        return null;
                    }
                    if (data.code && data.code.indexOf('USERNAME') === 0) {
                        markUsername('is-err');
                        setMsg(nameMsg, data.error || '该用户名已被注册，请更换', 'is-err');
                    }
                    setMsg(registerError, data.error || '注册失败，请重试', 'is-err');
                    return null;
                });
            }).catch(function () {
                setMsg(registerError, '网络异常，请检查网络后重试', 'is-err');
            }).then(function () {
                setBusy(registerSubmit, false);
            });
        });
    }
})();
