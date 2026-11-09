/* 模拟答题：计时、答题卡、逐题保存、交卷判分与解析。
   答案只在交卷后由服务端下发；本文件不包含任何正确答案常量。 */
(function () {
    'use strict';

    var root = document.getElementById('bank-exam');
    if (!root) return;

    var bank = {};
    try { bank = JSON.parse(root.getAttribute('data-bank') || '{}'); } catch (e) { bank = {}; }
    var sessionToken = root.getAttribute('data-session') || '';
    var loggedIn = root.getAttribute('data-logged-in') === '1';
    var bankStatus = root.getAttribute('data-bank-status') || '';

    var loading = document.getElementById('exam-loading');
    var gate = document.getElementById('exam-gate');
    var errorBox = document.getElementById('exam-error');
    var errorText = document.getElementById('exam-error-text');
    var layout = document.getElementById('exam-layout');
    var head = document.getElementById('exam-head');
    var resultBox = document.getElementById('exam-result');
    var clock = document.getElementById('exam-clock');
    var progressBar = document.getElementById('exam-progress');

    var state = {
        session: null,
        index: 0,
        answers: {},
        deadlineTs: 0,
        timer: null,
        saveTimer: null,
        saveChain: Promise.resolve(),
        pending: null,
        autoSubmitted: false,
        submitting: false
    };

    function esc(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function byId(id) { return document.getElementById(id); }

    function showOnly(el) {
        [loading, gate, errorBox, layout, resultBox].forEach(function (node) {
            if (node) node.hidden = node !== el;
        });
        // 成绩页不再显示答题页头部的题库与倒计时，避免「已交卷还在计时」的误解
        if (head) head.hidden = (el === resultBox);
    }

    function showGate(title, text) {
        showOnly(gate);
        byId('gate-title').textContent = title;
        byId('gate-text').textContent = text;
        var login = byId('gate-login');
        if (!loggedIn) {
            login.textContent = '去登录';
            login.href = '/login?next=' + encodeURIComponent(location.pathname);
            login.hidden = false;
        } else {
            login.hidden = true;
        }
    }

    function showError(message) {
        showOnly(errorBox);
        errorText.textContent = message || '请稍后重试';
    }

    async function api(path, options) {
        var opts = Object.assign({ credentials: 'same-origin' }, options || {});
        if (opts.body && !opts.headers) {
            opts.headers = { 'Content-Type': 'application/json' };
        }
        var res = await fetch('/api/practice' + path, opts);
        var data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        if (res.status === 401) {
            location.href = '/login?next=' + encodeURIComponent(location.pathname);
            throw new Error('请先登录');
        }
        if (!data) throw new Error('接口返回格式异常');
        if (!data.ok) {
            var err = new Error(data.error || '请求失败');
            err.payload = data;
            err.status = res.status;
            throw err;
        }
        return data.data;
    }

    function formatClock(seconds) {
        seconds = Math.max(0, Math.round(seconds));
        var h = Math.floor(seconds / 3600);
        var m = Math.floor((seconds % 3600) / 60);
        var s = seconds % 60;
        var mm = (m < 10 ? '0' : '') + m;
        var ss = (s < 10 ? '0' : '') + s;
        return h > 0 ? ((h < 10 ? '0' : '') + h + ':' + mm + ':' + ss) : (mm + ':' + ss);
    }

    function answersOf(qid) {
        var value = state.answers[String(qid)];
        return Array.isArray(value) ? value : [];
    }

    function answeredCount() {
        return state.session ? Number(state.session.answered || 0) : 0;
    }

    function updateProgress() {
        var total = state.session ? state.session.total : 0;
        var answered = answeredCount();
        if (progressBar) progressBar.style.width = (total ? answered / total * 100 : 0) + '%';
        var sheetProgress = byId('sheet-progress');
        if (sheetProgress) sheetProgress.textContent = '已答 ' + answered + '/' + total;
    }

    function updateIndexLabel() {
        var total = state.session ? state.session.total : 0;
        byId('exam-index').textContent = String(state.index + 1);
        byId('q-seq').textContent = '第 ' + (state.index + 1) + ' 题 / 共 ' + total + ' 题';
    }

    function renderSheet() {
        var grid = byId('sheet-grid');
        if (!grid || !state.session) return;
        grid.innerHTML = state.session.questions.map(function (q, index) {
            var answered = answersOf(q.id).length > 0;
            var cls = index === state.index ? 'is-current' : (answered ? 'is-answered' : '');
            return '<button type="button" class="' + cls + '" data-jump="' + index + '" ' +
                'aria-label="第 ' + (index + 1) + ' 题' + (answered ? '，已作答' : '，未作答') + '">' +
                (index + 1) + '</button>';
        }).join('');
        updateProgress();
    }

    function renderQuestion() {
        if (!state.session) return;
        var q = state.session.questions[state.index];
        if (!q) return;
        updateIndexLabel();

        byId('q-type').textContent = q.type_label || (q.type === 'multiple' ? '多选题' : '单选题');
        byId('q-type').className = 'q-type ' + (q.type === 'multiple' ? 'is-multiple' : 'is-single');
        byId('q-difficulty').textContent = q.difficulty || '中等';
        byId('q-stem').textContent = q.question;

        var selected = answersOf(q.id);
        var inputType = q.type === 'multiple' ? 'checkbox' : 'radio';
        var letters = Object.keys(q.options || {}).sort();
        byId('q-options').innerHTML = letters.map(function (letter) {
            var checked = selected.indexOf(letter) >= 0;
            return '<label class="bank-option' + (checked ? ' is-selected' : '') + '" data-letter="' + esc(letter) + '">' +
                '<input type="' + inputType + '" name="q-option" value="' + esc(letter) + '"' +
                (checked ? ' checked' : '') + '>' +
                '<span class="bank-option-letter">' + esc(letter) + '</span>' +
                '<span class="bank-option-text">' + esc(q.options[letter]) + '</span>' +
                '</label>';
        }).join('');

        Array.prototype.forEach.call(byId('q-options').querySelectorAll('input'), function (input) {
            input.addEventListener('change', function () {
                collectAnswer(q);
            });
        });

        byId('q-prev').disabled = state.index === 0;
        byId('q-next').disabled = state.index >= state.session.questions.length - 1;
        renderSheet();
    }

    function collectAnswer(q) {
        var letters = [];
        Array.prototype.forEach.call(byId('q-options').querySelectorAll('input:checked'), function (input) {
            letters.push(input.value);
        });
        letters.sort();
        if (letters.length) {
            state.answers[String(q.id)] = letters;
        } else {
            delete state.answers[String(q.id)];
        }
        Array.prototype.forEach.call(byId('q-options').querySelectorAll('.bank-option'), function (label) {
            var input = label.querySelector('input');
            label.classList.toggle('is-selected', !!(input && input.checked));
        });
        queueSave(q.id, letters);
        renderSheet();
    }

    function setSaveHint(text, kind) {
        var hint = byId('save-hint');
        if (!hint) return;
        hint.textContent = text || '';
        hint.className = 'bank-save-hint' + (kind ? ' is-' + kind : '');
    }

    function queueSave(qid, letters) {
        state.pending = { qid: qid, letters: letters };
        clearTimeout(state.saveTimer);
        state.saveTimer = setTimeout(flushSave, 320);
    }

    function flushSave() {
        if (!state.pending) return;
        var payload = state.pending;
        state.pending = null;
        setSaveHint('保存中…');
        state.saveChain = state.saveChain.then(function () {
            return sendAnswer(payload.qid, payload.letters);
        }).catch(function () { /* sendAnswer 内部已提示，链不中断 */ });
    }

    async function sendAnswer(qid, letters) {
        if (!state.session) return;
        try {
            var data = await api('/sessions/' + encodeURIComponent(state.session.token) + '/answer', {
                method: 'POST',
                body: JSON.stringify({ question_id: qid, answer: letters })
            });
            if (data.status === 'submitted') {
                handleSession(data);
                return;
            }
            state.session.answered = data.answered;
            updateProgress();
            setSaveHint('已保存 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false }), 'ok');
        } catch (err) {
            if (err.payload && err.payload.code === 'SESSION_SUBMITTED') {
                try {
                    var session = await api('/sessions/' + encodeURIComponent(state.session.token));
                    handleSession(session);
                    return;
                } catch (e) { /* 继续走失败提示 */ }
            }
            setSaveHint('保存失败，请检查网络后重试', 'err');
        }
    }

    function goTo(index) {
        if (!state.session) return;
        if (index < 0 || index >= state.session.questions.length || index === state.index) return;
        flushSave();
        state.index = index;
        renderQuestion();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function startTimer(seconds) {
        clearInterval(state.timer);
        state.deadlineTs = Date.now() + Math.max(0, Number(seconds || 0)) * 1000;
        tick();
        state.timer = setInterval(tick, 1000);
    }

    function tick() {
        if (!state.session || state.session.status !== 'ongoing') return;
        var remaining = Math.max(0, Math.round((state.deadlineTs - Date.now()) / 1000));
        clock.textContent = formatClock(remaining);
        clock.classList.toggle('is-urgent', remaining > 0 && remaining <= 300);
        if (remaining <= 0 && !state.autoSubmitted) {
            state.autoSubmitted = true;
            clock.textContent = '00:00';
            doSubmit('timeout');
        }
    }

    function updateUrl(token) {
        try {
            var url = new URL(location.href);
            if (token) url.searchParams.set('session', token);
            else url.searchParams.delete('session');
            history.replaceState(null, '', url.toString());
        } catch (e) { /* 老浏览器忽略 */ }
    }

    function handleSession(session) {
        if (!session) {
            showError('试卷数据为空');
            return;
        }
        if (session.status === 'submitted') {
            state.session = session;
            clearInterval(state.timer);
            updateUrl(session.token);
            renderResult(session.result, session);
            return;
        }
        state.session = session;
        state.answers = {};
        Object.keys(session.answers || {}).forEach(function (key) {
            state.answers[key] = session.answers[key];
        });
        state.index = Math.min(Math.max(0, Number(session.current_index || 0)), session.questions.length - 1);
        state.autoSubmitted = false;
        state.submitting = false;
        showOnly(layout);
        renderQuestion();
        startTimer(session.remaining_seconds);
        updateUrl(session.token);
        var submitBtns = [byId('q-submit'), byId('sheet-submit')];
        submitBtns.forEach(function (btn) { if (btn) btn.disabled = false; });
    }

    async function boot() {
        showOnly(loading);
        if (!loggedIn) {
            showGate('登录后开始练习', '练习进度、成绩与错题需要绑定账号保存。');
            return;
        }
        if (sessionToken) {
            try {
                handleSession(await api('/sessions/' + encodeURIComponent(sessionToken)));
                return;
            } catch (err) {
                if (err.status !== 404) {
                    showError(err.message);
                    return;
                }
            }
        }
        if (bankStatus === 'disabled') {
            showGate('题库已停用', '该题库已停止发布，暂不能开始新练习；历史成绩仍保留在个人中心。');
            return;
        }
        try {
            handleSession(await api('/banks/' + encodeURIComponent(bank.slug) + '/start', {
                method: 'POST',
                body: JSON.stringify({ restart: false })
            }));
        } catch (err) {
            showError(err.message);
        }
    }

    function openConfirm() {
        if (!state.session || state.submitting) return;
        flushSave();
        var unanswered = state.session.questions.filter(function (q) {
            return answersOf(q.id).length === 0;
        }).length;
        byId('confirm-text').textContent = unanswered
            ? '还有 ' + unanswered + ' 道题未作答，未作答题目按 0 分计。交卷后不可修改，确认交卷？'
            : '共 ' + state.session.total + ' 题已全部作答。交卷后不可修改，确认交卷？';
        byId('submit-confirm').hidden = false;
    }

    function closeConfirm() {
        byId('submit-confirm').hidden = true;
    }

    async function doSubmit(reason) {
        if (!state.session || state.submitting) return;
        state.submitting = true;
        closeConfirm();
        clearTimeout(state.saveTimer);
        flushSave();
        var submitBtns = [byId('q-submit'), byId('sheet-submit')];
        submitBtns.forEach(function (btn) { if (btn) { btn.disabled = true; btn.textContent = '正在交卷…'; } });
        try {
            await state.saveChain;
            var session = await api('/sessions/' + encodeURIComponent(state.session.token) + '/submit', {
                method: 'POST',
                body: JSON.stringify({ reason: reason || 'manual' })
            });
            handleSession(session);
        } catch (err) {
            state.submitting = false;
            submitBtns.forEach(function (btn) { if (btn) { btn.disabled = false; btn.textContent = '交卷并查看解析'; } });
            alert(err.message || '交卷失败，请重试');
        }
    }

    function answerText(options, letters) {
        if (!letters || !letters.length) return '未作答';
        return letters.map(function (letter) {
            var text = options && options[letter];
            return text ? letter + '. ' + text : letter;
        }).join('；');
    }

    function renderResult(result, session) {
        if (!result) {
            showError('成绩数据缺失');
            return;
        }
        showOnly(resultBox);
        var passed = !!result.passed;
        var badge = passed ? '合格' : '未合格';
        var reasonTip = result.submit_reason === 'timeout'
            ? '<div class="bank-result-timeout">考试时间已到，系统已自动交卷；已作答题目照常判分。</div>'
            : '';

        var stats = [
            ['正确题数', result.correct + ' / ' + result.total],
            ['正确率', result.accuracy + '%'],
            ['用时', formatClock(result.duration)],
            ['错题数', String(result.wrong_count)],
        ].map(function (item) {
            return '<div><b>' + esc(item[1]) + '</b><span>' + esc(item[0]) + '</span></div>';
        }).join('');

        var itemsHtml = (result.items || []).map(function (item) {
            var options = Object.keys(item.options || {}).sort();
            var revealed = item.revealed !== false;
            var optionHtml = options.map(function (letter) {
                var chosen = (item.your_answer || []).indexOf(letter) >= 0;
                var correct = revealed && (item.correct_answer || []).indexOf(letter) >= 0;
                var cls = 'bank-analysis-option';
                if (correct) cls += ' is-correct';
                if (chosen && !correct) cls += ' is-picked-wrong';
                else if (chosen) cls += ' is-picked';
                var marks = [];
                if (correct) marks.push('<em>正确答案</em>');
                if (chosen) marks.push('<em class="is-you">你的选择</em>');
                return '<div class="' + cls + '"><span class="letter">' + esc(letter) + '</span>' +
                    '<span class="text">' + esc(item.options[letter]) + '</span>' + marks.join('') + '</div>';
            }).join('');
            var status = !revealed
                ? '<span class="bank-analysis-status is-empty">未作答</span>'
                : (item.is_correct ? '<span class="bank-analysis-status is-right">正确</span>'
                    : '<span class="bank-analysis-status is-wrong">错误</span>');
            var answerLine = revealed
                ? '你的答案：<b>' + esc(answerText(item.options, item.your_answer)) +
                    '</b><br>正确答案：<b class="is-right">' + esc(answerText(item.options, item.correct_answer)) + '</b>'
                : '你的答案：<b>未作答</b><br><span class="muted">未作答的题目不显示答案与解析，可在下次练习时认真作答</span>';
            return '<article class="bank-analysis-item ' + (item.is_correct ? 'is-right' : 'is-wrong') +
                '" data-correct="' + (item.is_correct ? '1' : '0') + '">' +
                '<header><span class="bank-analysis-no">第 ' + (item.index + 1) + ' 题</span>' +
                '<span class="q-type ' + (item.type === 'multiple' ? 'is-multiple' : 'is-single') + '">' +
                esc(item.type_label) + '</span>' + status + '</header>' +
                '<h3>' + esc(item.question) + '</h3>' +
                '<div class="bank-analysis-options">' + optionHtml + '</div>' +
                '<p class="bank-analysis-answer">' + answerLine + '</p>' +
                (item.explanation_html
                    ? (item.is_correct
                        ? '<details class="bank-analysis-exp"><summary>查看解析</summary><div>' + item.explanation_html + '</div></details>'
                        : '<div class="bank-analysis-exp is-open"><b>解析</b><div>' + item.explanation_html + '</div></div>')
                    : '') +
                '</article>';
        }).join('');

        resultBox.innerHTML =
            '<div class="bank-result-head">' +
                '<div class="bank-score-ring ' + (passed ? 'is-pass' : 'is-fail') + '">' +
                    '<b>' + esc(result.score) + '</b><span>分</span>' +
                '</div>' +
                '<div class="bank-result-copy">' +
                    '<span class="bank-result-badge ' + (passed ? 'is-pass' : 'is-fail') + '">' + badge + '</span>' +
                    '<h2>' + esc(bank.name) + ' · 练习完成</h2>' +
                    '<p>满分 ' + esc(result.total_score) + ' 分，合格线 ' + esc(result.pass_score) + ' 分。' +
                    '本场共 ' + esc(result.total) + ' 题，答对 ' + esc(result.correct) + ' 题。</p>' +
                    (result.points_awarded > 0
                        ? '<p class="muted">本场获得 <b>' + esc(result.points_awarded) +
                            '</b> 积分，已计入日 / 周 / 月榜（同一题库每天只计一次）。</p>'
                        : '<p class="muted">本场未产生积分：同一题库每天只计一次，且需答完 60% 以上。</p>') +
                    reasonTip +
                    '<div class="bank-result-stats">' + stats + '</div>' +
                    '<div class="bank-result-actions">' +
                        '<button type="button" class="btn btn-primary" id="result-wrong">查看错题（' + esc(result.wrong_count) + '）</button>' +
                        '<button type="button" class="btn btn-ghost" id="result-retry">重新练习</button>' +
                        '<a class="btn btn-ghost" href="' +
                            (bank.cert_path ? '/cert/' + esc(bank.cert_path) : '/certification') +
                        '">返回' + esc(bank.cert_name || '认证页') + '</a>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="bank-result-body">' +
                '<div class="bank-result-body-head">' +
                    '<h2>逐题解析</h2>' +
                    '<span id="analysis-count">共 ' + esc(result.total) + ' 题</span>' +
                '</div>' +
                '<div class="bank-analysis" id="analysis-list">' + itemsHtml + '</div>' +
            '</div>';

        var wrongOnly = false;
        var wrongBtn = byId('result-wrong');
        if (wrongBtn) {
            wrongBtn.addEventListener('click', function () {
                wrongOnly = !wrongOnly;
                var items = resultBox.querySelectorAll('.bank-analysis-item');
                var shown = 0;
                Array.prototype.forEach.call(items, function (item) {
                    var hide = wrongOnly && item.getAttribute('data-correct') === '1';
                    item.hidden = hide;
                    if (!hide) shown += 1;
                });
                wrongBtn.textContent = wrongOnly ? '查看全部（' + result.total + '）' : '查看错题（' + result.wrong_count + '）';
                var countEl = byId('analysis-count');
                if (countEl) countEl.textContent = wrongOnly ? '仅显示错题 · ' + shown + ' 题' : '共 ' + result.total + ' 题';
                if (wrongOnly && shown === 0) {
                    countEl.textContent = '本场没有错题，全部答对';
                }
                window.scrollTo({ top: document.querySelector('.bank-result-body').offsetTop - 20, behavior: 'smooth' });
            });
        }
        var retryBtn = byId('result-retry');
        if (retryBtn) {
            retryBtn.addEventListener('click', async function () {
                retryBtn.disabled = true;
                retryBtn.textContent = '正在准备…';
                try {
                    var sessionNew = await api('/banks/' + encodeURIComponent(bank.slug) + '/start', {
                        method: 'POST',
                        body: JSON.stringify({ restart: true })
                    });
                    updateUrl('');
                    handleSession(sessionNew);
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                } catch (err) {
                    retryBtn.disabled = false;
                    retryBtn.textContent = '重新练习';
                    alert(err.message || '暂时无法重新练习');
                }
            });
        }
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // ------------------------------------------------------------------
    // 事件绑定
    // ------------------------------------------------------------------
    byId('q-prev').addEventListener('click', function () { goTo(state.index - 1); });
    byId('q-next').addEventListener('click', function () { goTo(state.index + 1); });
    byId('q-submit').addEventListener('click', openConfirm);
    byId('sheet-submit').addEventListener('click', openConfirm);
    byId('confirm-cancel').addEventListener('click', closeConfirm);
    byId('confirm-submit').addEventListener('click', function () { doSubmit('manual'); });
    byId('submit-confirm').addEventListener('click', function (e) {
        if (e.target === byId('submit-confirm')) closeConfirm();
    });
    byId('exam-retry').addEventListener('click', boot);
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeConfirm();
    });

    // 答题卡使用事件委托，避免每次渲染重复绑定
    var grid = byId('sheet-grid');
    if (grid) {
        grid.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-jump]');
            if (!btn) return;
            goTo(Number(btn.getAttribute('data-jump')));
        });
    }

    window.addEventListener('beforeunload', function (e) {
        if (state.session && state.session.status === 'ongoing' && !state.submitting) {
            flushSave();
            // 不弹浏览器确认框，避免打断正常离开；进度已逐题保存
        }
    });

    boot();
})();
