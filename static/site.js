/* 认证题库网 · 前端交互：首页榜单切换、每日答题、排行榜趋势图 */
(function () {
    'use strict';

    function esc(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    async function postJSON(url, payload) {
        var res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload || {})
        });
        var data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        return { status: res.status, ok: res.ok, data: data };
    }

    /* ---------------- 首页排行榜切换 ---------------- */
    var rankCard = document.getElementById('home-ranking');
    if (rankCard) {
        var listEl = rankCard.querySelector('#rank-list');
        rankCard.querySelectorAll('[data-period]').forEach(function (btn) {
            btn.addEventListener('click', async function () {
                var period = btn.getAttribute('data-period');
                rankCard.querySelectorAll('[data-period]').forEach(function (b) {
                    b.classList.toggle('active', b === btn);
                    b.setAttribute('aria-selected', b === btn ? 'true' : 'false');
                });
                listEl.innerHTML = '<li class="rank-empty">加载中…</li>';
                try {
                    var res = await fetch(rankCard.getAttribute('data-endpoint') + '?period=' + period + '&limit=8',
                        { credentials: 'same-origin' });
                    var payload = await res.json();
                    renderRankRows(listEl, payload.data && payload.data.rows || []);
                } catch (err) {
                    listEl.innerHTML = '<li class="rank-empty">加载失败，请稍后重试</li>';
                }
            });
        });
    }

    function renderRankRows(listEl, rows) {
        if (!rows.length) {
            listEl.innerHTML = '<li class="rank-empty">本期还没有人答题，来抢第一位吧。</li>';
            return;
        }
        listEl.innerHTML = rows.map(function (row) {
            return '<li class="rank-item rank-' + row.rank + '">' +
                '<span class="rank-no">' + row.rank + '</span>' +
                '<span class="rank-name" title="' + esc(row.display_name) + '">' + esc(row.display_name) + '</span>' +
                '<span class="rank-points">' + row.points + '<em>分</em></span>' +
                '<span class="rank-meta">' + row.quiz_count + ' 次 · 最高 ' + row.best_score + '</span>' +
                '</li>';
        }).join('');
    }

    /* ---------------- 每日答题 ---------------- */
    var setup = document.getElementById('quiz-setup');
    if (setup) {
        var tree = [];
        try { tree = JSON.parse(setup.getAttribute('data-tree') || '[]'); } catch (e) { tree = []; }
        var preselect = setup.getAttribute('data-preselect') || '';
        var loggedIn = setup.getAttribute('data-logged-in') === '1';

        var vendorSel = document.getElementById('pick-vendor');
        var dirSel = document.getElementById('pick-direction');
        var levelSel = document.getElementById('pick-level');
        var infoEl = document.getElementById('picker-info');
        var startBtn = document.getElementById('start-quiz');

        var play = document.getElementById('quiz-play');
        var resultPanel = document.getElementById('quiz-result');
        var currentLeaf = null;
        var state = { questions: [], index: 0, answers: {}, startedAt: 0, timer: null, categoryId: 0, poolLabel: '', source: '' };

        tree.forEach(function (v) {
            var opt = document.createElement('option');
            opt.value = v.slug; opt.textContent = v.label;
            vendorSel.appendChild(opt);
        });

        vendorSel.addEventListener('change', function () {
            dirSel.innerHTML = '<option value="">请选择方向</option>';
            levelSel.innerHTML = '<option value="">请先选择方向</option>';
            levelSel.disabled = true;
            currentLeaf = null;
            var vendor = tree.filter(function (v) { return v.slug === vendorSel.value; })[0];
            if (!vendor) { dirSel.disabled = true; updateInfo(); return; }
            dirSel.disabled = false;
            vendor.directions.forEach(function (d) {
                var opt = document.createElement('option');
                opt.value = d.slug;
                opt.textContent = d.label + '（' + (d.question_count || 0) + ' 题）';
                dirSel.appendChild(opt);
            });
            updateInfo();
        });

        dirSel.addEventListener('change', function () {
            levelSel.innerHTML = '';
            currentLeaf = null;
            var found = findDirection();
            if (!found) { levelSel.disabled = true; levelSel.innerHTML = '<option value="">请先选择方向</option>'; updateInfo(); return; }
            if (found.direction.single) {
                levelSel.disabled = true;
                levelSel.innerHTML = '<option value="">该方向为单一认证</option>';
                currentLeaf = {
                    path: found.vendor.slug + '/' + found.direction.slug,
                    name: found.direction.label,
                    count: found.direction.question_count || 0
                };
            } else {
                levelSel.disabled = false;
                levelSel.innerHTML = '<option value="">请选择等级</option>';
                found.direction.children.forEach(function (c) {
                    var opt = document.createElement('option');
                    opt.value = c.path;
                    opt.textContent = c.label + '（' + (c.count || 0) + ' 题）';
                    levelSel.appendChild(opt);
                });
            }
            updateInfo();
        });

        levelSel.addEventListener('change', function () {
            var found = findDirection();
            if (!found) return;
            var leaf = found.direction.children.filter(function (c) { return c.path === levelSel.value; })[0];
            currentLeaf = leaf ? { path: leaf.path, name: leaf.name, count: leaf.count || 0 } : null;
            updateInfo();
        });

        function findDirection() {
            var vendor = tree.filter(function (v) { return v.slug === vendorSel.value; })[0];
            if (!vendor) return null;
            var direction = vendor.directions.filter(function (d) { return d.slug === dirSel.value; })[0];
            if (!direction) return null;
            return { vendor: vendor, direction: direction };
        }

        function updateInfo() {
            if (!currentLeaf) {
                infoEl.innerHTML = '<span class="muted">选择完成后显示题量与难度</span>';
                return;
            }
            infoEl.innerHTML =
                '<span>已选：<b>' + esc(currentLeaf.name) + '</b></span>' +
                '<span>题量：<b>' + currentLeaf.count + '</b> 道</span>' +
                '<span>本次抽取：<b>10</b> 道 · 每题 <b>10</b> 分</span>';
        }

        startBtn.addEventListener('click', async function () {
            if (!currentLeaf) { alert('请先选择厂商、方向与等级'); return; }
            if (currentLeaf.count < 10) {
                alert('该认证题库不足 10 道题，系统会从同方向/同厂商题库补充；如果全站题目不足，请先在后台导入。');
            }
            if (!loggedIn) {
                location.href = '/login?next=' + encodeURIComponent('/daily-quiz?category=' + currentLeaf.path);
                return;
            }
            startBtn.disabled = true;
            startBtn.textContent = '正在抽题…';
            var res = await postJSON('/api/quiz/start', { category_id: currentLeaf.path });
            startBtn.disabled = false;
            startBtn.textContent = '开始答题';
            if (res.status === 401) {
                location.href = '/login?next=' + encodeURIComponent('/daily-quiz?category=' + currentLeaf.path);
                return;
            }
            if (!res.ok || !res.data || !res.data.ok) {
                alert((res.data && res.data.error) || '抽题失败，请稍后重试');
                return;
            }
            beginQuiz(res.data.data);
        });

        function beginQuiz(payload) {
            state.questions = payload.questions || [];
            state.index = 0;
            state.answers = {};
            state.startedAt = Date.now();
            state.categoryId = payload.category.id;
            state.poolLabel = payload.pool_label || '';
            state.source = payload.source || '';
            setup.hidden = true;
            resultPanel.hidden = true;
            play.hidden = false;
            document.getElementById('quiz-pool-label').textContent = state.poolLabel;
            startTimer();
            renderQuestion();
            window.scrollTo({ top: play.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
        }

        function startTimer() {
            clearInterval(state.timer);
            state.timer = setInterval(function () {
                var sec = Math.floor((Date.now() - state.startedAt) / 1000);
                document.getElementById('quiz-timer').textContent = fmtTime(sec);
            }, 1000);
        }

        function fmtTime(sec) {
            var m = Math.floor(sec / 60), s = sec % 60;
            return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
        }

        function renderQuestion() {
            var q = state.questions[state.index];
            if (!q) return;
            document.getElementById('quiz-progress-text').textContent = (state.index + 1) + ' / ' + state.questions.length;
            document.getElementById('quiz-progress-bar').style.width =
                ((state.index + 1) / state.questions.length * 100) + '%';
            document.getElementById('quiz-type').textContent =
                q.type === 'multiple' ? '多选题' : (q.type === 'judge' ? '判断题' : '单选题');
            document.getElementById('quiz-difficulty').textContent = q.difficulty || '中';
            document.getElementById('quiz-stem').textContent = q.question;

            var box = document.getElementById('quiz-options');
            box.innerHTML = '';
            var picked = state.answers[q.id] || [];
            Object.keys(q.options || {}).forEach(function (key) {
                var label = document.createElement('label');
                label.className = 'quiz-option' + (picked.indexOf(key) >= 0 ? ' checked' : '');
                var input = document.createElement('input');
                input.type = q.type === 'multiple' ? 'checkbox' : 'radio';
                input.name = 'q_' + q.id;
                input.value = key;
                input.checked = picked.indexOf(key) >= 0;
                input.addEventListener('change', function () { choose(q, key, input); });
                label.appendChild(input);
                var b = document.createElement('b'); b.textContent = key;
                label.appendChild(b);
                var span = document.createElement('span'); span.textContent = q.options[key];
                label.appendChild(span);
                box.appendChild(label);
            });

            document.getElementById('quiz-prev').disabled = state.index === 0;
            var isLast = state.index === state.questions.length - 1;
            document.getElementById('quiz-next').hidden = isLast;
            document.getElementById('quiz-submit').hidden = !isLast;
        }

        function choose(q, key, input) {
            var current = state.answers[q.id] || [];
            if (q.type === 'multiple') {
                if (input.checked) { if (current.indexOf(key) < 0) current.push(key); }
                else { current = current.filter(function (k) { return k !== key; }); }
            } else {
                current = [key];
            }
            state.answers[q.id] = current.sort();
            document.querySelectorAll('.quiz-option').forEach(function (el) {
                var inp = el.querySelector('input');
                el.classList.toggle('checked', inp.checked);
            });
        }

        document.getElementById('quiz-prev').addEventListener('click', function () {
            if (state.index > 0) { state.index -= 1; renderQuestion(); }
        });
        document.getElementById('quiz-next').addEventListener('click', function () {
            if (state.index < state.questions.length - 1) { state.index += 1; renderQuestion(); }
        });

        document.getElementById('quiz-submit').addEventListener('click', async function () {
            var unanswered = state.questions.filter(function (q) { return !(state.answers[q.id] || []).length; });
            if (unanswered.length && !confirm('还有 ' + unanswered.length + ' 道题未作答，确定交卷吗？')) return;
            var btn = this;
            btn.disabled = true; btn.textContent = '判分中…';
            var duration = Math.floor((Date.now() - state.startedAt) / 1000);
            var res = await postJSON('/api/quiz/submit', {
                category_id: state.categoryId,
                question_ids: state.questions.map(function (q) { return q.id; }),
                answers: state.answers,
                duration: duration,
                source: state.source
            });
            btn.disabled = false; btn.textContent = '交卷看结果';
            if (res.status === 401) {
                location.href = '/login?next=' + encodeURIComponent('/daily-quiz');
                return;
            }
            if (!res.ok || !res.data || !res.data.ok) {
                alert((res.data && res.data.error) || '交卷失败，请稍后重试');
                return;
            }
            clearInterval(state.timer);
            renderResult(res.data.data);
        });

        function renderResult(data) {
            play.hidden = true;
            resultPanel.hidden = false;
            document.getElementById('result-score').textContent = data.score;
            document.getElementById('result-correct').textContent = data.correct + '/' + data.total;
            document.getElementById('result-accuracy').textContent = Math.round(data.accuracy * 100) + '%';
            document.getElementById('result-time').textContent = fmtTime(data.duration);
            document.getElementById('score-ring').style.setProperty('--p', Math.min(100, data.score) + '%');

            var title = '继续加油';
            if (data.score === 100) title = '满分通过！';
            else if (data.score >= 80) title = '表现不错';
            else if (data.score >= 60) title = '基础过关';
            document.getElementById('result-title').textContent = title;
            document.getElementById('result-sub').textContent =
                (data.category ? data.category.cert_name : '') + ' · 成绩已记入个人中心与排行榜';

            var box = document.getElementById('review-list');
            box.innerHTML = data.review.map(function (item, idx) {
                var your = item.your_answer.length ? item.your_answer.join('、') : '未作答';
                var right = item.correct_answer.join('、');
                var opts = Object.keys(item.options || {}).map(function (k) {
                    return '<li><b>' + esc(k) + '</b><span>' + esc(item.options[k]) + '</span></li>';
                }).join('');
                return '<article class="review-item ' + (item.is_correct ? '' : 'wrong') + '">' +
                    '<div class="review-head"><span class="tag">第 ' + (idx + 1) + ' 题</span>' +
                    '<span class="' + (item.is_correct ? 'ok' : 'err') + '">' + (item.is_correct ? '答对' : '答错') + '</span></div>' +
                    '<p class="review-stem">' + esc(item.question) + '</p>' +
                    '<ul class="option-preview">' + opts + '</ul>' +
                    '<div class="review-answers"><span>你的答案：<b class="' + (item.is_correct ? 'right' : 'yours bad') + '">' + esc(your) + '</b></span>' +
                    '<span>正确答案：<b class="right">' + esc(right) + '</b></span></div>' +
                    (item.explanation ? '<p class="review-exp">解析：' + esc(item.explanation) + '</p>' : '') +
                    '</article>';
            }).join('');
            window.scrollTo({ top: resultPanel.getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
        }

        document.getElementById('retry-quiz').addEventListener('click', function () {
            resultPanel.hidden = true;
            play.hidden = true;
            setup.hidden = false;
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });

        // 预选：?category=aliyun/acp/cloud
        if (preselect) {
            var parts = preselect.split('/');
            vendorSel.value = parts[0];
            vendorSel.dispatchEvent(new Event('change'));
            if (parts[1]) {
                dirSel.value = parts[1];
                dirSel.dispatchEvent(new Event('change'));
                if (parts[2] && !levelSel.disabled) {
                    levelSel.value = preselect;
                    levelSel.dispatchEvent(new Event('change'));
                }
            }
        }
    }

    /* ---------------- 排行榜趋势图（内联 SVG，无第三方库） ---------------- */
    var chart = document.getElementById('trend-chart');
    if (chart) {
        var trend = [];
        try { trend = JSON.parse(chart.getAttribute('data-trend') || '[]'); } catch (e) { trend = []; }
        if (trend.length) renderTrend(chart, trend);
        else chart.innerHTML = '<div class="rank-empty">暂无数据</div>';
    }

    function renderTrend(target, data) {
        var W = 300, H = 140, padL = 10, padR = 10, padT = 14, padB = 24;
        var max = Math.max.apply(null, data.map(function (d) { return d.points; }).concat([100]));
        var stepX = (W - padL - padR) / Math.max(1, data.length - 1);
        var pts = data.map(function (d, i) {
            var x = padL + stepX * i;
            var y = padT + (H - padT - padB) * (1 - d.points / max);
            return { x: x, y: y, d: d };
        });
        var line = pts.map(function (p, i) { return (i ? 'L' : 'M') + p.x.toFixed(1) + ' ' + p.y.toFixed(1); }).join(' ');
        var area = line + ' L' + pts[pts.length - 1].x.toFixed(1) + ' ' + (H - padB) +
            ' L' + pts[0].x.toFixed(1) + ' ' + (H - padB) + ' Z';
        var dots = pts.map(function (p) {
            return '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="3" fill="#3b6ef5"></circle>';
        }).join('');
        var labels = pts.map(function (p, i) {
            if (i !== 0 && i !== pts.length - 1) return '';
            return '<text x="' + p.x.toFixed(1) + '" y="' + (H - 6) + '" font-size="10" fill="#8a94a6" text-anchor="' +
                (i === 0 ? 'start' : 'end') + '">' + p.d.date.slice(5) + '</text>';
        }).join('');
        var values = pts.map(function (p, i) {
            // 0 分的日子不标注数字，避免一排 0 糊在轴线上
            if (!p.d.points && i !== pts.length - 1) return '';
            return '<text x="' + p.x.toFixed(1) + '" y="' + (p.y - 8).toFixed(1) +
                '" font-size="10" fill="#2f59c7" text-anchor="middle">' + p.d.points + '</text>';
        }).join('');
        target.innerHTML =
            '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="100%" role="img" aria-label="近 7 天积分趋势">' +
            '<defs><linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="#3b6ef5" stop-opacity="0.22"/>' +
            '<stop offset="100%" stop-color="#3b6ef5" stop-opacity="0"/></linearGradient></defs>' +
            '<path d="' + area + '" fill="url(#trendFill)"></path>' +
            '<path d="' + line + '" fill="none" stroke="#3b6ef5" stroke-width="2" stroke-linejoin="round"></path>' +
            dots + values + labels + '</svg>';
    }
})();
