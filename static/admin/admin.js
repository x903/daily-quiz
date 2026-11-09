/* 独立管理端前端：只通过 /api/admin/* 取数与提交，不含任何用户端导航组件。 */
(function () {
    'use strict';

    var API = '/api/admin';
    var SECTIONS = {
        overview: { title: '控制台概览', sub: '题库、用户与答题数据的整体情况' },
        sets:     { title: '题库管理',   sub: '按日期维护题库，支持逐题编辑与整体删除' },
        import:   { title: '题库导入',   sub: '上传 UTF-8 Markdown / JSON，并指定所属三级认证栏目' },
        intros:   { title: '认证介绍管理', sub: '搜索并编辑三级菜单详情页首屏展示的认证百科介绍' },
        users:    { title: '用户与权限', sub: '普通用户（users）与管理员（admins）分表管理' },
        password: { title: '修改密码',   sub: '修改当前管理员密码；改后所有设备都需要用新密码重新登录' },
        records:  { title: '答题记录',   sub: '全部用户的成绩明细' },
        categories:{ title: '认证分类',   sub: '34 个三级认证分类：题量、难度、上架状态与 SEO' },
        questions: { title: '题库管理',   sub: '按认证分类检索、新增、编辑与删除题目' },
        practice:  { title: '模拟题库',   sub: '按真实考试规则组卷、发布，并查看练习人次与平均分' },
        rankings:  { title: '排行榜数据', sub: '按日 / 周 / 月查看积分榜' }
    };
    var view = document.getElementById('view');
    var titleEl = document.getElementById('page-title');
    var subEl = document.getElementById('page-sub');
    var toastEl = document.getElementById('toast');
    var modal = document.getElementById('modal');
    var modalTitle = document.getElementById('modal-title');
    var modalBody = document.getElementById('modal-body');
    var metaCache = null;
    var me = null;
    var toastTimer = null;

    function esc(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    async function api(path, options) {
        var opts = Object.assign({ credentials: 'same-origin' }, options || {});
        var res = await fetch(API + path, opts);
        var data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        // 只有带 ADMIN_AUTH_REQUIRED 的 401 才是会话失效；改密时「原密码错误」
        // 也走 4xx，不能让前端误以为掉线而跳登录页。
        if (res.status === 401 && (!data || !data.code || data.code === 'ADMIN_AUTH_REQUIRED')) {
            location.href = '/admin/login';
            throw new Error('管理员会话已失效');
        }
        if (!data) throw new Error('接口返回格式异常');
        if (!res.ok) {
            var err = new Error(data.error || '请求失败');
            err.payload = data;
            err.status = res.status;
            throw err;
        }
        return data;
    }

    function jsonBody(payload) {
        return {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        };
    }

    function toast(message, kind) {
        toastEl.textContent = message;
        toastEl.className = 'toast ' + (kind || '');
        toastEl.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toastEl.hidden = true; }, 3200);
    }

    function currentSection() {
        var key = (location.hash || '').replace('#', '').split('?')[0];
        return SECTIONS[key] ? key : 'overview';
    }

    function activateMenu(key) {
        Array.prototype.forEach.call(document.querySelectorAll('#admin-menu button'), function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-section') === key);
        });
        var cfg = SECTIONS[key] || SECTIONS.overview;
        titleEl.textContent = cfg.title;
        subEl.textContent = cfg.sub;
    }

    function openModal(title, html) {
        modalTitle.textContent = title;
        modalBody.innerHTML = html;
        modal.hidden = false;
    }
    function closeModal() { modal.hidden = true; modalBody.innerHTML = ''; }

    function setsTable(sets) {
        if (!sets || !sets.length) return '<div class="empty">暂无题库，请先在「题库导入」上传 Markdown。</div>';
        var rows = sets.map(function (s) {
            return '<tr>' +
                '<td>' + esc(s.date) + '</td>' +
                '<td>' + esc(s.title) + '</td>' +
                '<td class="muted">' + esc(s.category_path || '未分类') + '</td>' +
                '<td>' + esc(s.question_count) + '</td>' +
                '<td>' + esc(s.record_count) + '</td>' +
                '<td>' + esc(s.wrong_count) + '</td>' +
                '<td><div class="row-actions">' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-action="edit" data-date="' + esc(s.date) + '">编辑</button>' +
                    '<button type="button" class="btn btn-danger btn-sm" data-action="delete" data-date="' + esc(s.date) + '">删除</button>' +
                '</div></td></tr>';
        }).join('');
        return '<table><thead><tr><th>日期</th><th>标题</th><th>所属栏目</th><th>题数</th><th>成绩</th><th>错题</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table>';
    }

    function genericTable(rows, labels) {
        if (!rows || !rows.length) return '<div class="empty">暂无数据</div>';
        var keys = Object.keys(rows[0]).filter(function (k) { return k !== 'id'; });
        var head = keys.map(function (k) { return '<th>' + esc((labels && labels[k]) || k) + '</th>'; }).join('');
        var body = rows.map(function (row) {
            return '<tr>' + keys.map(function (k) {
                var val = row[k];
                if (typeof val === 'number' && k.indexOf('accuracy') >= 0) val = (val * 100).toFixed(1) + '%';
                if (val === null || val === undefined || val === '') val = '—';
                return '<td>' + esc(val) + '</td>';
            }).join('') + '</tr>';
        }).join('');
        return '<div style="overflow:auto"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>';
    }

    // ---------------------------------------------------------------- 栏目三级联动
    function leafPaths(roots) {
        var paths = [];
        function walk(node, path) {
            var p = path.concat([{ label: node.label, slug: node.slug }]);
            if (node.children && node.children.length) {
                node.children.forEach(function (c) { walk(c, p); });
            } else { paths.push(p); }
        }
        (roots || []).forEach(function (r) { walk(r, []); });
        return paths;
    }
    function uniqueNodes(nodes) {
        var seen = {}, out = [];
        nodes.forEach(function (n) { if (n && !seen[n.slug]) { seen[n.slug] = 1; out.push(n); } });
        return out;
    }
    function optionsHtml(nodes, placeholder, selected) {
        return '<option value="">' + esc(placeholder) + '</option>' + uniqueNodes(nodes).map(function (n) {
            return '<option value="' + esc(n.slug) + '"' + (n.slug === selected ? ' selected' : '') + '>' + esc(n.label) + '</option>';
        }).join('');
    }
    function initCategoryPicker(scope, roots, selectedSlug) {
        var s1 = scope.querySelector('[data-level="1"]');
        var s2 = scope.querySelector('[data-level="2"]');
        var s3 = scope.querySelector('[data-level="3"]');
        var hidden = scope.querySelector('[name="category_slug"]');
        if (!s1 || !hidden) return;
        var paths = leafPaths(roots);
        var pre = paths.filter(function (p) { return p.some(function (n) { return n.slug === selectedSlug; }); })[0];

        function reloadLevel2() {
            var c1 = s1.value;
            var level2 = paths.filter(function (p) { return p[0].slug === c1; }).map(function (p) { return p[1]; });
            s2.innerHTML = optionsHtml(level2, '请选择二级', pre && pre[1] ? pre[1].slug : '');
            reloadLevel3();
        }
        function reloadLevel3() {
            var c1 = s1.value, c2 = s2.value;
            var level3 = paths.filter(function (p) { return p[0].slug === c1 && p[1] && p[1].slug === c2 && p[2]; })
                              .map(function (p) { return p[2]; });
            s3.innerHTML = optionsHtml(level3, '请选择三级', pre && pre[2] ? pre[2].slug : '');
            // 二级本身即叶子时，第三级留空
            s3.disabled = !uniqueNodes(level3).length;
            syncValue();
        }
        function syncValue() {
            var c1 = s1.value, c2 = s2.value, c3 = s3.value;
            var hit = paths.filter(function (p) {
                return p[0].slug === c1 && (p.length < 2 || p[1].slug === c2) && (p.length < 3 || p[2].slug === c3);
            })[0];
            var complete = hit && (hit.length === 3 ? !!c3 : hit.length === 2 ? !!c2 : !!c1);
            hidden.value = complete ? hit[hit.length - 1].slug : '';
        }
        s1.innerHTML = optionsHtml(paths.map(function (p) { return p[0]; }), '请选择一级', pre ? pre[0].slug : '');
        reloadLevel2();
        s1.addEventListener('change', function () { pre = null; reloadLevel2(); });
        s2.addEventListener('change', function () { pre = null; reloadLevel3(); });
        s3.addEventListener('change', function () { pre = null; syncValue(); });
        syncValue();
    }

    function pickerHtml() {
        return '<div class="picker-row">' +
            '<div><label>一级栏目</label><select data-level="1"></select></div>' +
            '<div><label>二级栏目</label><select data-level="2"></select></div>' +
            '<div><label>三级栏目</label><select data-level="3"></select></div>' +
            '</div><input type="hidden" name="category_slug" value="">';
    }

    async function getMeta() {
        if (!metaCache) metaCache = (await api('/meta')).data;
        return metaCache;
    }

    // ---------------------------------------------------------------- 各视图
    async function renderOverview() {
        var r = await api('/overview');
        var s = r.data.stats;
        var kpis = [
            ['普通用户', s.user_count], ['题库日期', s.set_count], ['答题记录', s.record_count],
            ['错题总数', s.wrong_count], ['管理员', s.admin_count], ['认证介绍', s.intro_count || 0]
        ].map(function (p) {
            return '<div class="kpi"><div class="num">' + esc(p[1]) + '</div><div class="label">' + esc(p[0]) + '</div></div>';
        }).join('');
        view.innerHTML = '<div class="kpi-grid">' + kpis + '</div>' +
            '<div class="card"><h2>题库清单</h2><p class="hint">可在「题库管理」中逐题编辑或删除。</p>' + setsTable(r.data.sets) + '</div>' +
            '<div class="card"><h2>最近答题记录</h2>' + genericTable(r.data.recent_records, {
                username: '用户', set_date: '题库日期', correct: '答对', total: '总题数',
                accuracy: '正确率', finished_at: '完成时间'
            }) + '</div>';
    }

    async function renderSets() {
        var r = await api('/sets');
        view.innerHTML = '<div class="card"><h2>题库管理</h2><p class="hint">共 ' + r.data.sets.length + ' 个日期的题库；修改题目不会重算历史成绩。</p>' +
            '<div class="btn-row" style="margin-bottom:14px"><button type="button" class="btn" data-go="import">＋ 导入新题库</button>' +
            '<button type="button" class="btn btn-ghost" data-action="reload">刷新列表</button></div>' +
            setsTable(r.data.sets) + '</div>';
    }

    async function renderImport() {
        var meta = await getMeta();
        var today = new Date().toISOString().slice(0, 10);
        view.innerHTML = '<div class="card"><h2>上传 Markdown / JSON 题库</h2>' +
            '<p class="hint">支持 UTF-8 编码的 .md 与 .json 文件；日期可从文件名 / 标题 / JSON 的 date 字段识别，所属栏目必须选到三级菜单的最末一级。导入后试题自动显示在对应菜单下。</p>' +
            '<form id="import-form">' +
                '<div class="form-grid">' +
                    '<div><label for="imp-date">导入日期</label><input type="date" id="imp-date" name="date" value="' + today + '"><span class="field-tip">留空则自动从文件名或正文识别</span></div>' +
                    '<div><label for="imp-title">题库标题</label><input type="text" id="imp-title" name="title" placeholder="留空则用文件名"></div>' +
                    '<div><label for="imp-fmt">题库格式</label><select id="imp-fmt" name="fmt"><option value="">自动识别（推荐）</option><option value="markdown">Markdown</option><option value="json">JSON</option></select></div>' +
                '</div>' +
                '<label>所属栏目（厂商 › 方向 › 等级）</label>' + pickerHtml() +
                '<label for="imp-file">Markdown / JSON 文件</label><input type="file" id="imp-file" name="md_file" accept=".md,.markdown,.txt,.json,text/markdown,application/json" required>' +
                '<div class="btn-row" style="margin-top:18px"><button type="submit" class="btn" id="imp-submit">开始导入</button></div>' +
            '</form></div>' +
            '<div id="import-feedback"></div>';
        initCategoryPicker(view, meta.categories, '');
        var pending = null;
        var feedback = document.getElementById('import-feedback');

        async function submit(payload, overwrite) {
            var form = new FormData();
            Object.keys(payload).forEach(function (k) { form.append(k, payload[k]); });
            if (overwrite) form.append('confirm_overwrite', '1');
            return await api('/import', { method: 'POST', body: form });
        }
        function snapshot() {
            var file = document.getElementById('imp-file').files[0];
            var fmtSelect = document.getElementById('imp-fmt');
            return {
                date: document.getElementById('imp-date').value,
                title: document.getElementById('imp-title').value,
                category_slug: view.querySelector('[name="category_slug"]').value,
                fmt: fmtSelect ? fmtSelect.value : '',
                file: file
            };
        }
        document.getElementById('import-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var snap = snapshot();
            var payload = { date: snap.date, title: snap.title, category_slug: snap.category_slug, fmt: snap.fmt, md_file: snap.file };
            if (!snap.category_slug) { toast('请选择完整的三级栏目', 'err'); return; }
            var btn = document.getElementById('imp-submit');
            btn.disabled = true; btn.textContent = '导入中…';
            try {
                var r = await submit(payload, false);
                feedback.innerHTML = '<div class="card"><h2>导入完成</h2><p>' + esc(r.message) + '</p></div>';
                toast(r.message, 'ok');
                pending = null;
            } catch (err) {
                if (err.status === 409 && err.payload && err.payload.reason === 'exists') {
                    pending = err.payload.pending_content;
                    feedback.innerHTML = '<div class="card"><h2>该日期题库已存在</h2>' +
                        '<div class="notice">覆盖会替换原题库及其题目，历史成绩不会自动重算。请确认后再继续。</div>' +
                        '<div class="btn-row"><button type="button" class="btn btn-danger" id="imp-overwrite">确认覆盖导入</button>' +
                        '<button type="button" class="btn btn-ghost" id="imp-cancel">取消</button></div></div>';
                    document.getElementById('imp-cancel').onclick = function () { feedback.innerHTML = ''; pending = null; };
                    document.getElementById('imp-overwrite').onclick = async function () {
                        try {
                            var p = snapshot();
                            var r2 = await submit({
                                date: p.date, title: p.title, category_slug: p.category_slug,
                                fmt: p.fmt, markdown: pending
                            }, true);
                            feedback.innerHTML = '<div class="card"><h2>覆盖导入完成</h2><p>' + esc(r2.message) + '</p></div>';
                            toast(r2.message, 'ok');
                            pending = null;
                        } catch (e2) { toast(e2.message, 'err'); }
                    };
                } else {
                    toast(err.message, 'err');
                }
            } finally {
                btn.disabled = false; btn.textContent = '开始导入';
            }
        });
    }

    function adminsTable(rows) {
        if (!rows || !rows.length) return '<div class="empty">暂无管理员</div>';
        var currentId = me ? me.id : null;
        var body = rows.map(function (a) {
            var isSelf = currentId !== null && Number(a.id) === Number(currentId);
            var action = isSelf
                ? '<button type="button" class="btn btn-ghost btn-sm" data-go="password">修改我的密码</button>'
                : '<button type="button" class="btn btn-ghost btn-sm" data-reset="' + esc(a.id) +
                  '" data-name="' + esc(a.username) + '">重置密码</button>';
            return '<tr>' +
                '<td>' + esc(a.username) + (isSelf ? '<span class="tag-self">当前登录</span>' : '') + '</td>' +
                '<td>' + esc(a.role || 'admin') + '</td>' +
                '<td>' + esc(a.status || 'active') + '</td>' +
                '<td class="muted">' + esc(a.last_login_at || '—') + '</td>' +
                '<td class="muted">' + esc(a.created_at || '—') + '</td>' +
                '<td><div class="row-actions">' + action + '</div></td></tr>';
        }).join('');
        return '<div style="overflow:auto"><table><thead><tr><th>管理员账号</th><th>角色</th><th>状态</th>' +
            '<th>最近登录</th><th>创建时间</th><th>操作</th></tr></thead><tbody>' + body + '</tbody></table></div>';
    }

    function openResetPassword(id, username) {
        openModal('重置「' + username + '」的密码',
            '<form id="reset-form">' +
                '<label for="rs-new">新密码</label>' +
                '<input type="password" id="rs-new" autocomplete="new-password" required>' +
                '<span class="field-tip">8-64 位，需同时包含字母和数字</span>' +
                '<label for="rs-confirm">确认新密码</label>' +
                '<input type="password" id="rs-confirm" autocomplete="new-password" required>' +
                '<label for="rs-operator">你的密码（确认是本人操作）</label>' +
                '<input type="password" id="rs-operator" autocomplete="current-password" required>' +
                '<p class="field-msg is-err" id="rs-msg" hidden></p>' +
                '<div class="btn-row" style="margin-top:18px"><button type="submit" class="btn">确认重置</button>' +
                '<button type="button" class="btn btn-ghost" data-close>取消</button></div>' +
            '</form>');
        var msg = document.getElementById('rs-msg');
        function show(text) { msg.textContent = text; msg.hidden = false; }
        document.getElementById('reset-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            msg.hidden = true;
            var newPwd = document.getElementById('rs-new').value;
            var confirmPwd = document.getElementById('rs-confirm').value;
            var operatorPwd = document.getElementById('rs-operator').value;
            if (newPwd !== confirmPwd) { show('两次输入的新密码不一致'); return; }
            try {
                var res = await api('/admins/' + encodeURIComponent(id) + '/password', jsonBody({
                    new_password: newPwd,
                    confirm_password: confirmPwd,
                    operator_password: operatorPwd
                }));
                closeModal();
                toast(res.message, 'ok');
                await renderUsers();
            } catch (err) { show(err.message); }
        });
    }

    async function renderUsers() {
        var r = await api('/users');
        view.innerHTML = '<div class="card"><h2>普通用户（users 表）</h2>' + genericTable(r.data.users, {
            username: '用户名', role: '角色', record_count: '答题次数',
            avg_accuracy: '平均正确率', wrong_count: '错题数', created_at: '注册时间'
        }) + '</div>' +
        '<div class="card"><h2>管理员账号（admins 表）</h2>' +
        '<p class="hint">重置他人密码需要输入你自己的密码确认；被重置的账号在其他设备上的登录会立即失效。</p>' +
        adminsTable(r.data.admins) + '<form id="admin-create" class="form-grid" style="margin-top:16px">' +
            '<div><label>新增管理员账号</label><input type="text" name="username" required></div>' +
            '<div><label>初始密码（8-64 位，含字母和数字）</label><input type="password" name="password" required></div>' +
            '<div style="align-self:end"><button type="submit" class="btn">创建管理员</button></div>' +
        '</form></div>';
        document.getElementById('admin-create').addEventListener('submit', async function (e) {
            e.preventDefault();
            var f = new FormData(e.target);
            try {
                var res = await api('/admins', jsonBody({ username: f.get('username'), password: f.get('password') }));
                toast(res.message, 'ok');
                await renderUsers();
            } catch (err) { toast(err.message, 'err'); }
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-reset]'), function (btn) {
            btn.addEventListener('click', function () {
                openResetPassword(btn.getAttribute('data-reset'), btn.getAttribute('data-name'));
            });
        });
    }

    async function renderPassword() {
        view.innerHTML = '<div class="card" style="max-width:560px">' +
            '<h2>修改管理员密码</h2>' +
            '<p class="hint">当前账号：<b>' + esc(me ? me.username : '') + '</b>。新密码需 8-64 位，且同时包含字母和数字。</p>' +
            '<form id="pwd-form">' +
                '<label for="pwd-old">原密码</label>' +
                '<input type="password" id="pwd-old" autocomplete="current-password" required>' +
                '<label for="pwd-new">新密码</label>' +
                '<input type="password" id="pwd-new" autocomplete="new-password" required>' +
                '<span class="field-tip">不能与原密码相同，也不能包含空格</span>' +
                '<label for="pwd-confirm">确认新密码</label>' +
                '<input type="password" id="pwd-confirm" autocomplete="new-password" required>' +
                '<p class="field-msg is-err" id="pwd-msg" hidden></p>' +
                '<div class="btn-row" style="margin-top:18px">' +
                    '<button type="submit" class="btn" id="pwd-submit">保存新密码</button>' +
                '</div>' +
            '</form></div>' +
            '<div class="card" style="max-width:560px"><h2>改密后会怎样</h2>' +
            '<p class="hint">当前会话立即退出，其他设备上的管理端登录也会失效，需要用新密码重新登录。' +
            '如果忘记密码，可以请另一位管理员在「用户与权限」中重置。</p></div>';
        var msg = document.getElementById('pwd-msg');
        var btn = document.getElementById('pwd-submit');
        function show(text) { msg.textContent = text; msg.hidden = false; }
        document.getElementById('pwd-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            msg.hidden = true;
            var oldPwd = document.getElementById('pwd-old').value;
            var newPwd = document.getElementById('pwd-new').value;
            var confirmPwd = document.getElementById('pwd-confirm').value;
            if (newPwd.length < 8 || newPwd.length > 64) { show('密码长度需为 8-64 位'); return; }
            if (!/[A-Za-z]/.test(newPwd) || !/\d/.test(newPwd)) { show('密码需同时包含字母和数字'); return; }
            if (/\s/.test(newPwd)) { show('密码不能包含空格'); return; }
            if (newPwd === oldPwd) { show('新密码不能与原密码相同'); return; }
            if (newPwd !== confirmPwd) { show('两次输入的新密码不一致'); return; }
            btn.disabled = true;
            btn.textContent = '正在保存…';
            try {
                var res = await api('/password', jsonBody({
                    old_password: oldPwd,
                    new_password: newPwd,
                    confirm_password: confirmPwd
                }));
                toast(res.message || '密码已修改', 'ok');
                setTimeout(function () { location.href = res.redirect || '/admin/login?pwd=1'; }, 900);
            } catch (err) {
                show(err.message);
                btn.disabled = false;
                btn.textContent = '保存新密码';
            }
        });
    }

    async function renderRecords() {
        var r = await api('/records');
        view.innerHTML = '<div class="card"><h2>全部答题记录</h2><p class="hint">共 ' + r.data.records.length + ' 条</p>' +
            genericTable(r.data.records, {
                username: '用户', set_date: '题库日期', correct: '答对', wrong: '答错',
                total: '总题数', accuracy: '正确率', duration: '用时(秒)', finished_at: '完成时间'
            }) + '</div>';
    }

    // ---------------------------------------------------------------- 认证介绍管理（五段式 CMS）
    var introItems = [];

    async function renderIntros(keyword) {
        var q = keyword || '';
        var r = await api('/intros' + (q ? '?q=' + encodeURIComponent(q) : ''));
        introItems = r.data.items || [];
        var rows = introItems.map(function (it) {
            var text = it.positioning || it.summary || '';
            var brief = text.length > 60 ? text.slice(0, 60) + '…' : text;
            var level = it.level_name ? ' / ' + it.level_name : '';
            return '<tr>' +
                '<td><b>' + esc(it.cert_name) + '</b><div class="muted">' + esc(it.path_slug) + '</div></td>' +
                '<td class="muted">' + esc(it.vendor_name) + '<br>' + esc(it.direction_name) + esc(level) + '</td>' +
                '<td>' + esc(it.difficulty || it.cat_difficulty || '') + '</td>' +
                '<td class="intro-cell">' + (it.intro_id ? esc(brief) : '<span class="tag">未配置</span>') + '</td>' +
                '<td class="muted">' + esc(it.updated_at || '系统预置') +
                    (it.updated_by ? '<br>' + esc(it.updated_by) : '') + '</td>' +
                '<td><button type="button" class="btn btn-ghost btn-sm" data-intro-edit="' + it.category_id + '">编辑</button></td>' +
                '</tr>';
        }).join('');
        view.innerHTML = '<div class="card"><h2>认证介绍管理</h2>' +
            '<p class="hint">共 ' + esc(r.data.total) + ' 个认证分类。这里的「认证定位 / 适合人群 / 考试方向 / 难度 / 备考建议」' +
            '会直接渲染在用户端三级认证页首屏，保存后立即生效；未配置时回退到系统预置文案。</p>' +
            '<form id="intro-search" class="intro-search">' +
                '<input type="search" name="q" value="' + esc(q) + '" placeholder="搜索认证名称 / 厂商 / 路径">' +
                '<button type="submit" class="btn">搜索</button>' +
                (q ? '<button type="button" class="btn btn-ghost" id="intro-clear">清空</button>' : '') +
            '</form>' +
            (introItems.length
                ? '<div style="overflow:auto"><table><thead><tr><th>认证名称</th><th>厂商 / 方向</th><th>难度</th>' +
                  '<th>认证定位</th><th>最近更新</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
                : '<div class="empty">没有匹配的认证分类</div>') +
            '</div>';
        document.getElementById('intro-search').addEventListener('submit', function (e) {
            e.preventDefault();
            renderIntros(String(new FormData(e.target).get('q') || ''))
                .catch(function (err) { toast(err.message, 'err'); });
        });
        var clear = document.getElementById('intro-clear');
        if (clear) clear.addEventListener('click', function () {
            renderIntros('').catch(function (err) { toast(err.message, 'err'); });
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-intro-edit]'), function (btn) {
            btn.addEventListener('click', function () {
                var item = introItems.filter(function (x) {
                    return String(x.category_id) === btn.getAttribute('data-intro-edit');
                })[0];
                if (item) openIntroEditor(item);
            });
        });
    }

    function openIntroEditor(it) {
        var difficulty = it.difficulty || it.cat_difficulty || '进阶';
        openModal('编辑认证介绍 · ' + it.cert_name,
            '<div class="notice">' + esc(it.path_slug) + ' · ' + esc(it.vendor_name) + ' › ' +
            esc(it.direction_name) + (it.level_name ? ' › ' + esc(it.level_name) : '') +
            '。五个字段都会展示在用户端认证页首屏，保存后立即生效。</div>' +
            '<form id="intro-edit-form">' +
                '<label>认证定位</label><textarea name="positioning" rows="3" required>' + esc(it.positioning || '') + '</textarea>' +
                '<label>适合人群</label><textarea name="audience" rows="2" required>' + esc(it.audience || '') + '</textarea>' +
                '<label>考试方向</label><textarea name="exam_topics" rows="3" required>' + esc(it.exam_topics || '') + '</textarea>' +
                '<div class="form-grid" style="margin-top:10px">' +
                    '<div><label>难度</label><select name="difficulty">' +
                        ['入门', '进阶', '高级', '专家'].map(function (d) {
                            return '<option value="' + d + '"' + (d === difficulty ? ' selected' : '') + '>' + d + '</option>';
                        }).join('') + '</select></div>' +
                    '<div><label>资料来源</label><input type="text" name="source" value="' + esc(it.source || '') + '"></div>' +
                '</div>' +
                '<label>备考建议</label><textarea name="advice" rows="3" required>' + esc(it.advice || '') + '</textarea>' +
                '<label>一句话摘要（用于 SEO 描述兜底与首页卡片）</label>' +
                '<textarea name="summary" rows="2">' + esc(it.summary || '') + '</textarea>' +
                '<div class="btn-row" style="margin-top:16px"><button type="submit" class="btn">保存介绍</button>' +
                '<button type="button" class="btn btn-ghost" data-close>取消</button></div></form>');
        document.getElementById('intro-edit-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var f = new FormData(e.target);
            try {
                var res = await api('/intros/' + it.category_id, jsonBody({
                    positioning: f.get('positioning'), audience: f.get('audience'),
                    exam_topics: f.get('exam_topics'), difficulty: f.get('difficulty'),
                    advice: f.get('advice'), summary: f.get('summary'), source: f.get('source')
                }));
                toast(res.message, 'ok');
                closeModal();
                await renderIntros('');
            } catch (err) { toast(err.message, 'err'); }
        });
    }

    // ---------------------------------------------------------------- 编辑 / 删除
    async function openEditor(date) {
        var r = await api('/sets/' + encodeURIComponent(date));
        var d = r.data;
        var meta = await getMeta();
        var typeOptions = Object.keys(meta.type_labels).map(function (code) {
            return { code: code, label: meta.type_labels[code] };
        });
        var questions = d.questions.map(function (q) {
            var opts = (q.letters || []).map(function (letter) {
                return '<div class="option-row"><span>' + esc(letter) + '.</span>' +
                    '<input type="text" name="q_' + q.id + '_option_' + esc(letter) + '" value="' + esc(q.options[letter] || '') + '" placeholder="选项内容（留空忽略）"></div>';
            }).join('');
            var types = typeOptions.map(function (t) {
                return '<option value="' + esc(t.code) + '"' + (t.code === q.type ? ' selected' : '') + '>' + esc(t.label) + '</option>';
            }).join('');
            return '<div class="q-card" id="q-card-' + q.id + '"><h3>第 ' + esc(q.set_index) + ' 题 <span class="muted">ID ' + q.id + '</span>' +
                '<label class="del"><input type="checkbox" name="q_' + q.id + '_delete" value="1" onchange="this.closest(\'.q-card\').classList.toggle(\'marked\', this.checked)"> 删除本题</label></h3>' +
                '<label>题型</label><select name="q_' + q.id + '_type">' + types + '</select>' +
                '<label>题干</label><textarea name="q_' + q.id + '_question" rows="2" required>' + esc(q.question) + '</textarea>' +
                '<label>选项</label>' + opts +
                '<label>正确答案</label><input type="text" name="q_' + q.id + '_answer" value="' + esc((q.answer || []).join('')) + '" required>' +
                '<label>解析</label><textarea name="q_' + q.id + '_explanation" rows="3">' + esc(q.explanation || '') + '</textarea></div>';
        }).join('');

        openModal('编辑题库 · ' + date,
            '<div class="notice">当前 ' + esc(d.stats.question_count) + ' 道题、' + esc(d.stats.record_count) +
            ' 条成绩、' + esc(d.stats.wrong_count) + ' 条错题。修改题目不会重算历史成绩。</div>' +
            '<form id="edit-form"><div class="form-grid">' +
                '<div><label>日期</label><input type="date" name="set_date" value="' + esc(date) + '" required></div>' +
                '<div><label>标题</label><input type="text" name="set_title" value="' + esc(d.set.title) + '" required></div>' +
            '</div><label>所属栏目（不选表示保持原归属：' + esc(d.set.category_path || '未分类') + '）</label>' + pickerHtml() +
            '<h2 style="font-size:15px;margin-top:18px">题目内容</h2>' + questions +
            '<div class="btn-row" style="margin-top:16px"><button type="submit" class="btn">保存修改</button>' +
            '<button type="button" class="btn btn-ghost" data-close>取消</button></div></form>');
        initCategoryPicker(modalBody, meta.categories, '');
        document.getElementById('edit-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            try {
                var res = await api('/sets/' + encodeURIComponent(date), { method: 'POST', body: new FormData(e.target) });
                toast(res.message, 'ok');
                closeModal();
                render();
            } catch (err) { toast(err.message, 'err'); }
        });
    }

    async function openDelete(date) {
        var r = await api('/sets/' + encodeURIComponent(date) + '/delete-preview');
        var s = r.data.stats;
        openModal('删除题库 · ' + date,
            '<div class="notice">将永久删除「' + esc(r.data.set.title) + '」：' + esc(s.question_count) + ' 道题、' +
            esc(s.record_count) + ' 条成绩、' + esc(s.wrong_count) + ' 条错题，且不可恢复。</div>' +
            '<label>请输入日期 <b>' + esc(date) + '</b> 以确认</label>' +
            '<input type="text" id="confirm-date" placeholder="' + esc(date) + '">' +
            '<div class="btn-row" style="margin-top:16px"><button type="button" class="btn btn-danger" id="confirm-delete">确认删除</button>' +
            '<button type="button" class="btn btn-ghost" data-close>取消</button></div>');
        document.getElementById('confirm-delete').addEventListener('click', async function () {
            var value = document.getElementById('confirm-date').value.trim();
            if (value !== date) { toast('请准确输入日期后再删除', 'err'); return; }
            try {
                var res = await api('/sets/' + encodeURIComponent(date), {
                    method: 'DELETE', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirm_text: value })
                });
                toast(res.message, 'ok');
                closeModal();
                render();
            } catch (err) { toast(err.message, 'err'); }
        });
    }


    // ================================================================
    // 认证题库网 v2：认证分类 / 题库管理 / 题库导入 / 排行榜
    // ================================================================
    async function loadCategoryOptions() {
        var res = await api('/cert-categories');
        return res.data.items || [];
    }

    function categoryOptionsHtml(items, selected) {
        var byVendor = {};
        items.forEach(function (c) {
            (byVendor[c.vendor_name] = byVendor[c.vendor_name] || []).push(c);
        });
        return Object.keys(byVendor).map(function (vendor) {
            return '<optgroup label="' + esc(vendor) + '">' + byVendor[vendor].map(function (c) {
                return '<option value="' + c.id + '"' +
                    (String(selected) === String(c.id) ? ' selected' : '') + '>' +
                    esc(c.cert_name) + '（' + c.question_count + ' 题）</option>';
            }).join('') + '</optgroup>';
        }).join('');
    }

    async function renderCategories(keyword) {
        var res = await api('/cert-categories' + (keyword ? '?q=' + encodeURIComponent(keyword) : ''));
        var items = res.data.items || [];
        var rows = items.map(function (c) {
            return '<tr>' +
                '<td><b>' + esc(c.cert_name) + '</b><div class="muted">' + esc(c.path_slug) +
                    (c.exam_code ? ' · ' + esc(c.exam_code) : '') + '</div></td>' +
                '<td>' + esc(c.vendor_name) + ' / ' + esc(c.direction_name) + '</td>' +
                '<td>' + esc(c.question_count) + '</td>' +
                '<td>' + esc(c.difficulty) + '</td>' +
                '<td>' + (c.status === 'active' ? '<span class="tag">上架</span>' : '<span class="tag">下架</span>') + '</td>' +
                '<td><div class="row-actions"><button type="button" class="btn btn-ghost btn-sm" data-cat-edit="' + c.id + '">编辑</button></div></td>' +
                '</tr>';
        }).join('');
        view.innerHTML = '<div class="card">' +
            '<div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">' +
                '<input id="cat-q" type="text" placeholder="搜索认证名称 / 路径 / 考试代码" style="flex:1;min-width:220px" value="' + esc(keyword || '') + '">' +
                '<button type="button" class="btn" id="cat-search">搜索</button>' +
            '</div>' +
            '<div class="muted" style="margin-bottom:10px">共 ' + items.length + ' 个认证分类，题库总量 ' +
                esc((res.data.stats || {}).questions || 0) + ' 道</div>' +
            '<table><thead><tr><th>认证</th><th>厂商 / 方向</th><th>题量</th><th>难度</th><th>状态</th><th>操作</th></tr></thead>' +
            '<tbody>' + rows + '</tbody></table></div>';
        document.getElementById('cat-search').addEventListener('click', function () {
            renderCategories(document.getElementById('cat-q').value.trim()).catch(function (e) { toast(e.message, 'err'); });
        });
        document.getElementById('cat-q').addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); document.getElementById('cat-search').click(); }
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-cat-edit]'), function (btn) {
            btn.addEventListener('click', function () {
                var item = items.filter(function (c) { return String(c.id) === btn.getAttribute('data-cat-edit'); })[0];
                if (item) openCategoryEditor(item);
            });
        });
    }

    function openCategoryEditor(c) {
        openModal('编辑认证分类 · ' + c.cert_name,
            '<div class="notice">分类名称与 SEO 会直接展示在用户端认证页；下架后该认证不再允许开始答题。</div>' +
            '<form id="cat-form">' +
                '<label>认证名称</label><input type="text" name="cert_name" value="' + esc(c.cert_name) + '" required>' +
                '<div class="form-grid" style="margin-top:10px">' +
                    '<div><label>考试代码</label><input type="text" name="exam_code" value="' + esc(c.exam_code) + '"></div>' +
                    '<div><label>难度</label><select name="difficulty">' +
                        ['入门', '进阶', '高级', '专家'].map(function (d) {
                            return '<option value="' + d + '"' + (d === c.difficulty ? ' selected' : '') + '>' + d + '</option>';
                        }).join('') + '</select></div>' +
                '</div>' +
                '<div class="form-grid" style="margin-top:10px">' +
                    '<div><label>状态</label><select name="status">' +
                        '<option value="active"' + (c.status === 'active' ? ' selected' : '') + '>上架</option>' +
                        '<option value="hidden"' + (c.status !== 'active' ? ' selected' : '') + '>下架</option>' +
                    '</select></div>' +
                    '<div><label>排序</label><input type="number" name="sort_order" value="' + esc(c.sort_order) + '"></div>' +
                '</div>' +
                '<label style="margin-top:10px">SEO 标题</label><input type="text" name="seo_title" value="' + esc(c.seo_title) + '">' +
                '<label>SEO 关键词</label><input type="text" name="seo_keywords" value="' + esc(c.seo_keywords) + '">' +
                '<label>SEO 描述</label><textarea name="seo_description" rows="2">' + esc(c.seo_description) + '</textarea>' +
                '<div class="btn-row" style="margin-top:16px"><button type="submit" class="btn">保存</button>' +
                '<button type="button" class="btn btn-ghost" data-close>取消</button></div>' +
            '</form>');
        document.getElementById('cat-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var f = new FormData(e.target);
            try {
                var res = await api('/cert-categories/' + c.id, jsonBody({
                    cert_name: f.get('cert_name'), exam_code: f.get('exam_code'),
                    difficulty: f.get('difficulty'), status: f.get('status'),
                    sort_order: f.get('sort_order'), seo_title: f.get('seo_title'),
                    seo_keywords: f.get('seo_keywords'), seo_description: f.get('seo_description')
                }));
                toast(res.message, 'ok');
                closeModal();
                await renderCategories('');
            } catch (err) { toast(err.message, 'err'); }
        });
    }

    async function renderQuestions(keyword, categoryId, page) {
        var cats = await loadCategoryOptions();
        var query = '?page=' + (page || 1);
        if (categoryId) query += '&category_id=' + encodeURIComponent(categoryId);
        if (keyword) query += '&q=' + encodeURIComponent(keyword);
        var res = await api('/questions' + query);
        var d = res.data;
        var rows = (d.questions || []).map(function (q) {
            return '<tr>' +
                '<td style="max-width:420px"><b>' + esc(q.question) + '</b><div class="muted">ID ' + q.id +
                    ' · 答案 ' + esc(q.answer) + '</div></td>' +
                '<td>' + esc(q.cert_name || '') + '</td>' +
                '<td>' + ({ single: '单选', multiple: '多选', judge: '判断' }[q.type] || q.type) + '</td>' +
                '<td>' + esc(q.difficulty) + '</td>' +
                '<td><div class="row-actions">' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-q-edit="' + q.id + '">编辑</button>' +
                    '<button type="button" class="btn btn-danger btn-sm" data-q-del="' + q.id + '">删除</button>' +
                '</div></td></tr>';
        }).join('');
        view.innerHTML = '<div class="card">' +
            '<div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap;align-items:center">' +
                '<select id="q-cat" style="min-width:220px"><option value="">全部分类</option>' +
                    categoryOptionsHtml(cats, categoryId) + '</select>' +
                '<input id="q-kw" type="text" placeholder="搜索题干 / 解析" value="' + esc(keyword || '') + '" style="flex:1;min-width:180px">' +
                '<button type="button" class="btn btn-ghost" id="q-search">搜索</button>' +
                '<button type="button" class="btn" id="q-new">+ 新增题目</button>' +
            '</div>' +
            '<div class="muted" style="margin-bottom:10px">共 ' + d.total + ' 道题 · 第 ' + d.page + '/' + d.pages + ' 页</div>' +
            '<table><thead><tr><th>题目</th><th>认证分类</th><th>题型</th><th>难度</th><th>操作</th></tr></thead>' +
            '<tbody>' + rows + (rows ? '' : '<tr><td colspan="5" class="empty">暂无题目</td></tr>') + '</tbody></table>' +
            '<div class="btn-row" style="margin-top:14px">' +
                (d.page > 1 ? '<button type="button" class="btn btn-ghost btn-sm" id="q-prev">上一页</button>' : '') +
                (d.page < d.pages ? '<button type="button" class="btn btn-ghost btn-sm" id="q-next">下一页</button>' : '') +
            '</div></div>';
        var catSel = document.getElementById('q-cat');
        document.getElementById('q-search').addEventListener('click', function () {
            renderQuestions(document.getElementById('q-kw').value.trim(), catSel.value, 1).catch(function (e) { toast(e.message, 'err'); });
        });
        catSel.addEventListener('change', function () {
            renderQuestions(document.getElementById('q-kw').value.trim(), catSel.value, 1).catch(function (e) { toast(e.message, 'err'); });
        });
        document.getElementById('q-new').addEventListener('click', function () {
            openQuestionEditor(null, cats, catSel.value || (cats[0] && cats[0].id));
        });
        if (document.getElementById('q-prev')) {
            document.getElementById('q-prev').addEventListener('click', function () {
                renderQuestions(keyword, catSel.value, d.page - 1).catch(function (e) { toast(e.message, 'err'); });
            });
        }
        if (document.getElementById('q-next')) {
            document.getElementById('q-next').addEventListener('click', function () {
                renderQuestions(keyword, catSel.value, d.page + 1).catch(function (e) { toast(e.message, 'err'); });
            });
        }
        Array.prototype.forEach.call(view.querySelectorAll('[data-q-edit]'), function (btn) {
            btn.addEventListener('click', async function () {
                try {
                    var id = btn.getAttribute('data-q-edit');
                    var one = await api('/questions?page=1&q=');
                    var found = (one.data.questions || []).filter(function (q) { return String(q.id) === id; })[0];
                    if (!found) {
                        var list = await api('/questions?page=1&category_id=' + encodeURIComponent(catSel.value || ''));
                        found = (list.data.questions || []).filter(function (q) { return String(q.id) === id; })[0];
                    }
                    if (found) openQuestionEditor(found, cats, found.cert_category_id);
                } catch (err) { toast(err.message, 'err'); }
            });
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-q-del]'), function (btn) {
            btn.addEventListener('click', async function () {
                if (!confirm('确认删除这道题？删除后不可恢复。')) return;
                try {
                    var res = await api('/questions/' + btn.getAttribute('data-q-del') + '/delete', { method: 'POST' });
                    toast(res.message, 'ok');
                    await renderQuestions(keyword, catSel.value, d.page);
                } catch (err) { toast(err.message, 'err'); }
            });
        });
    }

    function openQuestionEditor(q, cats, defaultCategory) {
        var options = q ? q.options : { A: '', B: '', C: '', D: '' };
        var optionRows = ['A', 'B', 'C', 'D'].map(function (key) {
            return '<div class="option-row"><span>' + key + '.</span><input type="text" name="opt_' + key +
                '" value="' + esc(options[key] || '') + '"></div>';
        }).join('');
        openModal(q ? '编辑题目 · ID ' + q.id : '新增题目',
            '<form id="q-form">' +
                '<label>认证分类</label><select name="category_id" required>' +
                    categoryOptionsHtml(cats, q ? q.cert_category_id : defaultCategory) + '</select>' +
                '<div class="form-grid" style="margin-top:10px">' +
                    '<div><label>题型</label><select name="type">' +
                        [['single', '单选题'], ['multiple', '多选题'], ['judge', '判断题']].map(function (t) {
                            return '<option value="' + t[0] + '"' + (q && q.type === t[0] ? ' selected' : '') + '>' + t[1] + '</option>';
                        }).join('') + '</select></div>' +
                    '<div><label>难度</label><select name="difficulty">' +
                        ['易', '中', '难'].map(function (d) {
                            return '<option value="' + d + '"' + (q && q.difficulty === d ? ' selected' : '') + '>' + d + '</option>';
                        }).join('') + '</select></div>' +
                '</div>' +
                '<label style="margin-top:10px">题干</label><textarea name="question" rows="3" required>' + esc(q ? q.question : '') + '</textarea>' +
                '<label>选项</label>' + optionRows +
                '<label>正确答案（多选写 A,B）</label><input type="text" name="answer" value="' + esc(q ? q.answer : '') + '" required>' +
                '<label>解析</label><textarea name="explanation" rows="3">' + esc(q ? q.explanation : '') + '</textarea>' +
                '<div class="btn-row" style="margin-top:16px"><button type="submit" class="btn">保存</button>' +
                '<button type="button" class="btn btn-ghost" data-close>取消</button></div>' +
            '</form>');
        document.getElementById('q-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var f = new FormData(e.target);
            var optionsObj = {};
            ['A', 'B', 'C', 'D'].forEach(function (key) {
                var value = String(f.get('opt_' + key) || '').trim();
                if (value) optionsObj[key] = value;
            });
            var body = {
                category_id: f.get('category_id'),
                type: f.get('type'),
                difficulty: f.get('difficulty'),
                question: f.get('question'),
                options: optionsObj,
                answer: f.get('answer'),
                explanation: f.get('explanation')
            };
            try {
                var res = q
                    ? await api('/questions/' + q.id, jsonBody(body))
                    : await api('/questions', jsonBody(body));
                toast(res.message, 'ok');
                closeModal();
                await renderQuestions('', body.category_id, 1);
            } catch (err) { toast(err.message, 'err'); }
        });
    }

    async function renderBankImport() {
        var cats = await loadCategoryOptions();
        view.innerHTML = '<div class="card">' +
            '<div class="notice">导入前必须选择三级认证分类。导入成功后会自动生成一个已发布的「模拟题库」，题目同时进入该认证的模拟题库列表与每日答题抽题池，学员可针对性练习。</div>' +
            '<form id="bank-import-form">' +
                '<label>认证分类（必选）</label><select name="category_id" required>' +
                    '<option value="">请选择认证分类</option>' + categoryOptionsHtml(cats, '') + '</select>' +
                '<label style="margin-top:12px">上传文件（Markdown / JSON）</label>' +
                '<input type="file" name="file" accept=".md,.markdown,.json,.txt">' +
                '<label style="margin-top:12px">或粘贴题目内容</label>' +
                '<textarea name="markdown" rows="10" placeholder="支持 Markdown 题目格式，或 JSON questions 数组"></textarea>' +
                '<label style="margin-top:12px"><input type="checkbox" name="overwrite" value="1"> 覆盖该分类下的现有题目</label>' +
                '<div class="btn-row" style="margin-top:16px"><button type="submit" class="btn">开始导入</button></div>' +
            '</form><div id="import-result" style="margin-top:16px"></div></div>';
        document.getElementById('bank-import-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var form = e.target;
            var data = new FormData(form);
            if (!data.get('category_id')) { toast('请选择认证分类', 'err'); return; }
            var btn = form.querySelector('button[type=submit]');
            btn.disabled = true; btn.textContent = '导入中…';
            try {
                var res = await api('/bank/import', { method: 'POST', body: data });
                var d = res.data || {};
                document.getElementById('import-result').innerHTML =
                    '<div class="notice">' + esc(res.message) + '（失败 ' + esc(d.failed || 0) + ' 道）</div>';
                toast(res.message, 'ok');
                form.reset();
            } catch (err) {
                toast(err.message, 'err');
            } finally {
                btn.disabled = false; btn.textContent = '开始导入';
            }
        });
    }

    async function renderRankings(period) {
        period = period || 'day';
        var res = await api('/rankings?period=' + period + '&limit=50');
        var d = res.data;
        var rows = (d.rows || []).map(function (r) {
            return '<tr><td>' + r.rank + '</td><td>' + esc(r.display_name) + '</td><td>' + r.points +
                '</td><td>' + r.quiz_count + '</td><td>' + r.best_score + '</td><td class="muted">' + esc(r.updated_at) + '</td></tr>';
        }).join('');
        view.innerHTML = '<div class="card">' +
            '<div class="btn-row" style="margin-bottom:14px">' +
                [['day', '日榜'], ['week', '周榜'], ['month', '月榜']].map(function (p) {
                    return '<button type="button" class="btn ' + (p[0] === period ? '' : 'btn-ghost') +
                        '" data-rank-period="' + p[0] + '">' + p[1] + '</button>';
                }).join('') +
            '</div>' +
            '<div class="muted" style="margin-bottom:10px">周期：' + esc(d.period_key) + ' · 共 ' + (d.rows || []).length + ' 人</div>' +
            '<table><thead><tr><th>排名</th><th>昵称</th><th>积分</th><th>答题次数</th><th>最高分</th><th>更新时间</th></tr></thead>' +
            '<tbody>' + (rows || '<tr><td colspan="6" class="empty">本期暂无数据</td></tr>') + '</tbody></table></div>';
        Array.prototype.forEach.call(view.querySelectorAll('[data-rank-period]'), function (btn) {
            btn.addEventListener('click', function () {
                renderRankings(btn.getAttribute('data-rank-period')).catch(function (e) { toast(e.message, 'err'); });
            });
        });
    }

    // ---------------------------------------------------------------- 自由练习题库
    function bankPill(status) {
        var labels = { published: '已发布', draft: '草稿', disabled: '已停用' };
        return '<span class="bank-pill is-' + esc(status) + '">' + esc(labels[status] || status) + '</span>';
    }

    function bankCategoryOptionsHtml(items, selectedPath) {
        var byVendor = {};
        (items || []).forEach(function (c) {
            (byVendor[c.vendor_name] = byVendor[c.vendor_name] || []).push(c);
        });
        return Object.keys(byVendor).map(function (vendor) {
            return '<optgroup label="' + esc(vendor) + '">' + byVendor[vendor].map(function (c) {
                return '<option value="' + esc(c.path_slug) + '"' +
                    (selectedPath === c.path_slug ? ' selected' : '') + '>' +
                    esc(c.cert_name) + '</option>';
            }).join('') + '</optgroup>';
        }).join('');
    }

    async function renderPracticeBanks(keyword, status) {
        var query = '?q=' + encodeURIComponent(keyword || '') + '&status=' + encodeURIComponent(status || '');
        var res = await api('/practice-banks' + query);
        var d = res.data || {};
        var s = d.summary || {};
        var rows = (d.banks || []).map(function (b) {
            var hint = (b.publish_errors && b.publish_errors.length)
                ? '<div class="pb-row-hint">' + esc(b.publish_errors[0]) + '</div>' : '';
            var toggleLabel = b.status === 'published' ? '停用' : '发布';
            var toggleStatus = b.status === 'published' ? 'disabled' : 'published';
            return '<tr>' +
                '<td><b>' + esc(b.name) + '</b><div class="muted">/' + esc(b.slug) +
                    ' · 更新 ' + esc((b.updated_at || '').slice(0, 10)) + '</div></td>' +
                '<td>' + esc(b.cert_name) + '<div class="muted">' + esc(b.level_tag) + ' · ' + esc(b.difficulty) + '</div></td>' +
                '<td><b>' + esc(b.question_count) + '</b> 题' +
                    '<div class="muted">每卷 ' + esc(b.total_questions) + ' 题 · ' +
                    esc(b.single_count) + ' 单选 · ' + esc(b.multiple_count) + ' 多选</div></td>' +
                '<td>' + esc(b.rule_name) + '<div class="muted">' + esc(b.duration_minutes) + ' 分钟 · ' +
                    esc(b.pass_score) + ' 分合格</div></td>' +
                '<td><b>' + esc(b.attempts || 0) + '</b> 人次 / ' + esc(b.participants || 0) + ' 人' +
                    '<div class="muted">平均 ' + esc(b.avg_score || 0) + ' 分</div></td>' +
                '<td>' + bankPill(b.status) + hint + '</td>' +
                '<td><div class="row-actions">' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-pb-questions="' + b.id + '">题目</button>' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-pb-edit="' + b.id + '">编辑</button>' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-pb-toggle="' + b.id + '" data-status="' + toggleStatus + '">' + toggleLabel + '</button>' +
                    '<button type="button" class="btn btn-danger-ghost btn-sm" data-pb-del="' + b.id + '">删除</button>' +
                '</div></td></tr>';
        }).join('');
        var statusOptions = [
            ['', '全部状态'], ['published', '已发布'], ['draft', '草稿'], ['disabled', '已停用']
        ].map(function (item) {
            return '<option value="' + item[0] + '"' + (status === item[0] ? ' selected' : '') + '>' + item[1] + '</option>';
        }).join('');

        view.innerHTML = '<div class="kpi-grid">' +
                '<div class="kpi"><div class="num">' + esc(s.total || 0) + '</div><div class="label">题库总数</div></div>' +
                '<div class="kpi"><div class="num">' + esc(s.published || 0) + '</div><div class="label">已发布</div></div>' +
                '<div class="kpi"><div class="num">' + esc(s.attempts || 0) + '</div><div class="label">累计练习人次</div></div>' +
                '<div class="kpi"><div class="num">' + esc(s.avg_score || 0) + '</div><div class="label">整体平均分</div></div>' +
            '</div>' +
            '<div class="card">' +
                '<div class="pb-toolbar">' +
                    '<input id="pb-q" type="search" placeholder="搜索题库 / 认证" value="' + esc(keyword || '') + '">' +
                    '<select id="pb-status">' + statusOptions + '</select>' +
                    '<button type="button" class="btn btn-ghost" id="pb-search">搜索</button>' +
                    '<button type="button" class="btn" id="pb-new">+ 新建题库</button>' +
                    '<button type="button" class="btn btn-ghost" id="pb-rules">考试规则</button>' +
                '</div>' +
                '<div class="notice">发布校验：题库实际题量、单选/多选数量必须与所选考试规则完全一致；' +
                    '不满足时只能保存为草稿，无法发布到前端。</div>' +
                '<table class="pb-table"><thead><tr><th>题库</th><th>认证</th><th>实际 / 声明题量</th><th>规则</th>' +
                    '<th>练习数据</th><th>状态</th><th>操作</th></tr></thead><tbody>' +
                    (rows || '<tr><td colspan="7" class="empty">暂无题库，点击「新建题库」开始组卷</td></tr>') +
                '</tbody></table>' +
            '</div>';

        var qInput = document.getElementById('pb-q');
        var statusSel = document.getElementById('pb-status');
        function reload() {
            renderPracticeBanks(qInput.value.trim(), statusSel.value).catch(function (e) { toast(e.message, 'err'); });
        }
        document.getElementById('pb-search').addEventListener('click', reload);
        statusSel.addEventListener('change', reload);
        qInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') reload(); });

        document.getElementById('pb-new').addEventListener('click', async function () {
            try {
                var cats = await loadCategoryOptions();
                openBankEditor(null, d.rules || [], cats);
            } catch (err) { toast(err.message, 'err'); }
        });
        document.getElementById('pb-rules').addEventListener('click', function () {
            openExamRules().catch(function (e) { toast(e.message, 'err'); });
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-pb-questions]'), function (btn) {
            btn.addEventListener('click', function () {
                renderBankQuestions(Number(btn.getAttribute('data-pb-questions'))).catch(function (e) { toast(e.message, 'err'); });
            });
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-pb-edit]'), function (btn) {
            btn.addEventListener('click', async function () {
                try {
                    var bank = (d.banks || []).filter(function (x) { return String(x.id) === btn.getAttribute('data-pb-edit'); })[0];
                    var cats = await loadCategoryOptions();
                    openBankEditor(bank, d.rules || [], cats);
                } catch (err) { toast(err.message, 'err'); }
            });
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-pb-toggle]'), function (btn) {
            btn.addEventListener('click', async function () {
                var next = btn.getAttribute('data-status');
                var tip = next === 'published' ? '发布后前端题库目录立即可见，确认发布？' : '停用后前端不再展示该题库，历史成绩仍保留，确认停用？';
                if (!confirm(tip)) return;
                try {
                    var out = await api('/practice-banks/' + btn.getAttribute('data-pb-toggle') + '/status', jsonBody({ status: next }));
                    toast(out.message, 'ok');
                    reload();
                } catch (err) {
                    if (err.payload && err.payload.details) {
                        openModal('发布校验未通过', '<div class="notice is-danger">' +
                            err.payload.details.map(function (x) { return '<div>• ' + esc(x) + '</div>'; }).join('') +
                            '</div><p class="muted">请补充题目或调整题量后再发布。</p>');
                    } else {
                        toast(err.message, 'err');
                    }
                }
            });
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-pb-del]'), function (btn) {
            btn.addEventListener('click', async function () {
                if (!confirm('删除题库会同时删除题库内的题目，且不可恢复；已有答题记录的题库不能删除。确认继续？')) return;
                try {
                    var out = await api('/practice-banks/' + btn.getAttribute('data-pb-del') + '/delete', { method: 'POST' });
                    toast(out.message, 'ok');
                    reload();
                } catch (err) { toast(err.message, 'err'); }
            });
        });
    }

    function openBankEditor(bank, rules, cats) {
        var isNew = !bank;
        var ruleCode = bank ? bank.rule_code : ((rules[0] && rules[0].code) || 'custom');
        var ruleOptions = rules.map(function (r) {
            return '<option value="' + esc(r.code) + '"' + (ruleCode === r.code ? ' selected' : '') + '>' +
                esc(r.name) + '（' + esc(r.total_questions) + ' 题 / ' + esc(r.duration_minutes) + ' 分钟）</option>';
        }).join('') + '<option value="custom"' + (ruleCode === 'custom' ? ' selected' : '') + '>自定义规则</option>';

        var values = {
            total: bank ? bank.total_questions : 30,
            single: bank ? bank.single_count : 20,
            multiple: bank ? bank.multiple_count : 10,
            score: bank ? bank.total_score : 30,
            pass: bank ? bank.pass_score : 24,
            duration: bank ? bank.duration_minutes : 45
        };

        openModal(isNew ? '新建模拟题库' : '编辑题库 · ' + bank.name,
            '<form id="pb-form">' +
                '<div class="form-grid">' +
                    '<div><label>题库名称</label><input id="pbf-name" type="text" value="' + esc(bank ? bank.name : '') + '" placeholder="如 VPC 网络架构模拟卷" required></div>' +
                    '<div><label>URL 标识（可留空自动生成）</label><input id="pbf-slug" type="text" value="' + esc(bank ? bank.slug : '') + '" placeholder="vpc-architecture"></div>' +
                '</div>' +
                '<div class="form-grid">' +
                    '<div><label>所属认证</label><select id="pbf-cert">' + bankCategoryOptionsHtml(cats, bank ? bank.cert_path : 'aliyun/acp/cloud') + '</select></div>' +
                    '<div><label>分类标签</label><select id="pbf-level">' +
                        ['基础', '进阶', '冲刺', '错题本'].map(function (x) {
                            return '<option' + (bank && bank.level_tag === x ? ' selected' : '') + '>' + x + '</option>';
                        }).join('') + '</select></div>' +
                    '<div><label>难度</label><select id="pbf-difficulty">' +
                        ['简单', '中等', '困难'].map(function (x) {
                            return '<option' + (bank && bank.difficulty === x ? ' selected' : '') + '>' + x + '</option>';
                        }).join('') + '</select></div>' +
                '</div>' +
                '<label>考试规则</label><select id="pbf-rule">' + ruleOptions + '</select>' +
                '<div class="pb-rule-hint" id="pbf-rule-hint"></div>' +
                '<div class="form-grid">' +
                    '<div><label>总题数</label><input id="pbf-total" type="number" min="1" max="300" value="' + values.total + '"></div>' +
                    '<div><label>单选题数</label><input id="pbf-single" type="number" min="0" value="' + values.single + '"></div>' +
                    '<div><label>多选题数</label><input id="pbf-multiple" type="number" min="0" value="' + values.multiple + '"></div>' +
                    '<div><label>满分</label><input id="pbf-score" type="number" min="1" value="' + values.score + '"></div>' +
                    '<div><label>及格分</label><input id="pbf-pass" type="number" min="0" value="' + values.pass + '"></div>' +
                    '<div><label>考试时长（分钟）</label><input id="pbf-duration" type="number" min="1" max="600" value="' + values.duration + '"></div>' +
                '</div>' +
                '<label>题库简介</label><textarea id="pbf-desc" rows="2" placeholder="一句话说明题库覆盖范围">' + esc(bank ? bank.description : '') + '</textarea>' +
                '<label>排序（数字越小越靠前）</label><input id="pbf-sort" type="number" value="' + (bank ? bank.sort_order : 0) + '">' +
                '<div class="btn-row" style="margin-top:16px"><button type="submit" class="btn">保存题库</button>' +
                '<button type="button" class="btn btn-ghost" data-close>取消</button></div>' +
            '</form>');

        var ruleSel = document.getElementById('pbf-rule');
        var lockedIds = ['pbf-total', 'pbf-single', 'pbf-multiple', 'pbf-score', 'pbf-pass', 'pbf-duration'];
        var fieldMap = {
            total: 'pbf-total', single: 'pbf-single', multiple: 'pbf-multiple',
            score: 'pbf-score', pass: 'pbf-pass', duration: 'pbf-duration'
        };
        function ruleOf(code) {
            return (rules || []).filter(function (r) { return r.code === code; })[0] || null;
        }
        function syncRule() {
            var rule = ruleOf(ruleSel.value);
            lockedIds.forEach(function (id) {
                var el = document.getElementById(id);
                el.readOnly = !!rule;
                el.classList.toggle('is-locked', !!rule);
            });
            if (rule) {
                document.getElementById('pbf-total').value = rule.total_questions;
                document.getElementById('pbf-single').value = rule.single_count;
                document.getElementById('pbf-multiple').value = rule.multiple_count;
                document.getElementById('pbf-score').value = rule.total_score;
                document.getElementById('pbf-pass').value = rule.pass_score;
                document.getElementById('pbf-duration').value = rule.duration_minutes;
                document.getElementById('pbf-rule-hint').textContent = rule.description +
                    '（题库将严格按此规则校验，题量不可单独修改）';
            } else {
                document.getElementById('pbf-rule-hint').textContent = '自定义规则：请确保 单选题数 + 多选题数 = 总题数，发布时实际题目必须与声明完全一致。';
            }
        }
        ruleSel.addEventListener('change', syncRule);
        syncRule();

        document.getElementById('pb-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var body = {
                name: document.getElementById('pbf-name').value.trim(),
                slug: document.getElementById('pbf-slug').value.trim(),
                cert_path: document.getElementById('pbf-cert').value,
                level_tag: document.getElementById('pbf-level').value,
                difficulty: document.getElementById('pbf-difficulty').value,
                description: document.getElementById('pbf-desc').value.trim(),
                rule_code: ruleSel.value,
                total_questions: Number(document.getElementById('pbf-total').value || 0),
                single_count: Number(document.getElementById('pbf-single').value || 0),
                multiple_count: Number(document.getElementById('pbf-multiple').value || 0),
                total_score: Number(document.getElementById('pbf-score').value || 0),
                pass_score: Number(document.getElementById('pbf-pass').value || 0),
                duration_minutes: Number(document.getElementById('pbf-duration').value || 0),
                sort_order: Number(document.getElementById('pbf-sort').value || 0)
            };
            try {
                var out = bank
                    ? await api('/practice-banks/' + bank.id, jsonBody(body))
                    : await api('/practice-banks', jsonBody(body));
                toast(out.message, 'ok');
                closeModal();
                await renderPracticeBanks('', '');
            } catch (err) { toast(err.message, 'err'); }
        });
    }

    async function renderBankQuestions(bankId) {
        var res = await api('/practice-banks/' + bankId);
        var d = res.data || {};
        var bank = d.bank || {};
        var stats = d.stats || {};
        var questions = d.questions || [];
        var errors = d.publish_errors || [];
        var rows = questions.map(function (q) {
            return '<tr>' +
                '<td class="muted">' + q.id + '</td>' +
                '<td>' + (q.type === 'multiple' ? '<span class="bank-pill is-multiple">多选</span>' : '<span class="bank-pill is-single">单选</span>') + '</td>' +
                '<td class="pb-q-text"><b>' + esc(q.question) + '</b></td>' +
                '<td>' + esc((q.answer || []).join(',')) + '</td>' +
                '<td class="muted">' + (q.explanation ? '有' : '—') + '</td>' +
                '<td><div class="row-actions">' +
                    '<button type="button" class="btn btn-ghost btn-sm" data-pbq-edit="' + q.id + '">编辑</button>' +
                    '<button type="button" class="btn btn-danger btn-sm" data-pbq-del="' + q.id + '">删除</button>' +
                '</div></td></tr>';
        }).join('');
        var errorHtml = errors.length
            ? '<div class="notice is-danger"><b>当前未达到发布条件：</b>' +
                errors.map(function (x) { return '<div>• ' + esc(x) + '</div>'; }).join('') + '</div>'
            : '';
        var toggleLabel = bank.status === 'published' ? '停用题库' : '发布题库';
        var toggleStatus = bank.status === 'published' ? 'disabled' : 'published';

        view.innerHTML = '<div class="card">' +
                '<div class="pb-detail-head">' +
                    '<div><button type="button" class="btn btn-ghost btn-sm" id="pb-back">← 返回题库列表</button>' +
                        '<h2>' + esc(bank.name) + '</h2>' +
                        '<p class="muted">' + esc(bank.cert_name) + ' · ' + esc(bank.rule_name) + ' · ' +
                            esc(bank.question_count) + '/' + esc(bank.total_questions) + ' 题 · ' +
                            esc(bank.single_count) + ' 单选 + ' + esc(bank.multiple_count) + ' 多选</p></div>' +
                    '<div class="row-actions">' +
                        '<button type="button" class="btn" id="pb-add">+ 新增题目</button>' +
                        '<button type="button" class="btn btn-ghost" id="pb-import">批量导入</button>' +
                        '<button type="button" class="btn btn-ghost" id="pb-toggle" data-status="' + toggleStatus + '">' + toggleLabel + '</button>' +
                    '</div>' +
                '</div>' + errorHtml +
                '<div class="kpi-grid" style="margin-top:16px">' +
                    '<div class="kpi"><div class="num">' + esc(bank.question_count) + '</div><div class="label">实际题量 / 声明 ' + esc(bank.total_questions) + '</div></div>' +
                    '<div class="kpi"><div class="num">' + esc(stats.attempts || 0) + '</div><div class="label">累计练习人次</div></div>' +
                    '<div class="kpi"><div class="num">' + esc(stats.participants || 0) + '</div><div class="label">参与人数</div></div>' +
                    '<div class="kpi"><div class="num">' + esc(stats.avg_score || 0) + '</div><div class="label">平均得分</div></div>' +
                    '<div class="kpi"><div class="num">' + esc(stats.pass_rate || 0) + '%</div><div class="label">合格率</div></div>' +
                '</div>' +
            '</div>' +
            '<div class="card"><h2>题目列表（' + questions.length + '）</h2>' +
                '<table><thead><tr><th>ID</th><th>题型</th><th>题干</th><th>答案</th><th>解析</th><th>操作</th></tr></thead><tbody>' +
                (rows || '<tr><td colspan="6" class="empty">还没有题目，点击「新增题目」或「批量导入」</td></tr>') +
                '</tbody></table></div>';

        document.getElementById('pb-back').addEventListener('click', function () {
            renderPracticeBanks('', '').catch(function (e) { toast(e.message, 'err'); });
        });
        document.getElementById('pb-add').addEventListener('click', function () {
            openPracticeQuestionEditor(bankId, null);
        });
        document.getElementById('pb-import').addEventListener('click', function () {
            openPracticeImport(bankId);
        });
        document.getElementById('pb-toggle').addEventListener('click', async function () {
            var next = this.getAttribute('data-status');
            try {
                var out = await api('/practice-banks/' + bankId + '/status', jsonBody({ status: next }));
                toast(out.message, 'ok');
                await renderBankQuestions(bankId);
            } catch (err) {
                if (err.payload && err.payload.details) {
                    openModal('发布校验未通过', '<div class="notice is-danger">' +
                        err.payload.details.map(function (x) { return '<div>• ' + esc(x) + '</div>'; }).join('') +
                        '</div><p class="muted">请补充题目或调整题量后再发布。</p>');
                } else { toast(err.message, 'err'); }
            }
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-pbq-edit]'), function (btn) {
            btn.addEventListener('click', function () {
                var q = questions.filter(function (x) { return String(x.id) === btn.getAttribute('data-pbq-edit'); })[0];
                openPracticeQuestionEditor(bankId, q);
            });
        });
        Array.prototype.forEach.call(view.querySelectorAll('[data-pbq-del]'), function (btn) {
            btn.addEventListener('click', async function () {
                if (!confirm('确认删除这道题？删除后该题的历史作答记录也会一并删除。')) return;
                try {
                    var out = await api('/practice-questions/' + btn.getAttribute('data-pbq-del') + '/delete', { method: 'POST' });
                    toast(out.message, 'ok');
                    await renderBankQuestions(bankId);
                } catch (err) { toast(err.message, 'err'); }
            });
        });
    }

    function openPracticeQuestionEditor(bankId, q) {
        var options = q ? (q.options || {}) : { A: '', B: '', C: '', D: '' };
        var optionRows = ['A', 'B', 'C', 'D'].map(function (key) {
            return '<div class="option-row"><span>' + key + '.</span><input id="pbq-opt-' + key + '" type="text" value="' +
                esc(options[key] || '') + '"></div>';
        }).join('');
        openModal(q ? '编辑题目 · ID ' + q.id : '新增题目',
            '<form id="pbq-form">' +
                '<div class="form-grid">' +
                    '<div><label>题型</label><select id="pbq-type">' +
                        '<option value="single"' + (q && q.type === 'single' ? ' selected' : '') + '>单选题</option>' +
                        '<option value="multiple"' + (q && q.type === 'multiple' ? ' selected' : '') + '>多选题</option>' +
                    '</select></div>' +
                    '<div><label>难度</label><select id="pbq-difficulty">' +
                        ['简单', '中等', '困难'].map(function (x) {
                            return '<option' + (q && q.difficulty === x ? ' selected' : '') + '>' + x + '</option>';
                        }).join('') + '</select></div>' +
                '</div>' +
                '<label>题干</label><textarea id="pbq-question" rows="4" required>' + esc(q ? q.question : '') + '</textarea>' +
                '<label>选项（至少两个，留空的选项会被忽略）</label>' + optionRows +
                '<label>正确答案（单选写 B，多选写 A,C）</label><input id="pbq-answer" type="text" value="' +
                    esc(q ? (q.answer || []).join(',') : '') + '" placeholder="A,C" required>' +
                '<label>解析</label><textarea id="pbq-exp" rows="4">' + esc(q ? q.explanation : '') + '</textarea>' +
                '<div class="btn-row" style="margin-top:16px"><button type="submit" class="btn">保存题目</button>' +
                '<button type="button" class="btn btn-ghost" data-close>取消</button></div>' +
            '</form>');
        document.getElementById('pbq-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var optionsObj = {};
            ['A', 'B', 'C', 'D'].forEach(function (key) {
                var value = document.getElementById('pbq-opt-' + key).value.trim();
                if (value) optionsObj[key] = value;
            });
            var body = {
                type: document.getElementById('pbq-type').value,
                question: document.getElementById('pbq-question').value.trim(),
                options: optionsObj,
                answer: document.getElementById('pbq-answer').value.trim(),
                explanation: document.getElementById('pbq-exp').value.trim(),
                difficulty: document.getElementById('pbq-difficulty').value
            };
            try {
                var out = q
                    ? await api('/practice-questions/' + q.id, jsonBody(body))
                    : await api('/practice-banks/' + bankId + '/questions', jsonBody(body));
                toast(out.message, 'ok');
                closeModal();
                await renderBankQuestions(bankId);
            } catch (err) { toast(err.message, 'err'); }
        });
    }

    function openPracticeImport(bankId) {
        openModal('批量导入题目',
            '<div class="notice">支持与「题库导入」相同的 Markdown / JSON 格式；模拟题库只接收单选题和多选题，判断题会被跳过。</div>' +
            '<form id="pb-import-form">' +
                '<label>上传文件（.md / .json / .txt）</label><input id="pb-import-file" type="file" accept=".md,.markdown,.json,.txt">' +
                '<label style="margin-top:12px">或直接粘贴题目内容</label><textarea id="pb-import-text" rows="12" placeholder="**1.** 题干&#10;- A. 选项&#10;**答案：B**&#10;**解析：** ..."></textarea>' +
                '<div class="btn-row" style="margin-top:16px"><button type="submit" class="btn">开始导入</button>' +
                '<button type="button" class="btn btn-ghost" data-close>取消</button></div>' +
            '</form>');
        document.getElementById('pb-import-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var form = new FormData();
            var text = document.getElementById('pb-import-text').value.trim();
            var file = document.getElementById('pb-import-file').files[0];
            if (text) form.append('markdown', text);
            if (file) form.append('file', file);
            if (!text && !file) { toast('请粘贴内容或选择文件', 'err'); return; }
            var btn = e.target.querySelector('button[type=submit]');
            btn.disabled = true; btn.textContent = '导入中…';
            try {
                var out = await api('/practice-banks/' + bankId + '/import', { method: 'POST', body: form });
                var result = out.data.result || {};
                toast(out.message, 'ok');
                closeModal();
                await renderBankQuestions(bankId);
                if ((result.failed || []).length) {
                    openModal('部分题目导入失败',
                        '<div class="notice is-danger">' + result.failed.map(function (x) {
                            return '<div>• ' + esc(x.question || '') + '：' + esc(x.reason || '') + '</div>';
                        }).join('') + '</div>');
                }
            } catch (err) {
                toast(err.message, 'err');
            } finally {
                btn.disabled = false; btn.textContent = '开始导入';
            }
        });
    }

    async function openExamRules() {
        var res = await api('/exam-rules');
        var rules = res.data.rules || [];
        var cards = rules.map(function (rule) {
            return '<form class="rule-card" data-rule="' + esc(rule.code) + '">' +
                '<div class="rule-card-head"><h3>' + esc(rule.name) + '</h3>' +
                    '<span class="muted">' + esc(rule.code) + ' · 关联 ' + esc(rule.bank_count || 0) + ' 个题库</span></div>' +
                '<div class="form-grid">' +
                    '<div><label>规则名称</label><input name="name" type="text" value="' + esc(rule.name) + '" required></div>' +
                    '<div><label>总题数</label><input name="total_questions" type="number" min="1" value="' + esc(rule.total_questions) + '" required></div>' +
                    '<div><label>单选题数</label><input name="single_count" type="number" min="0" value="' + esc(rule.single_count) + '" required></div>' +
                    '<div><label>多选题数</label><input name="multiple_count" type="number" min="0" value="' + esc(rule.multiple_count) + '" required></div>' +
                    '<div><label>满分</label><input name="total_score" type="number" min="1" value="' + esc(rule.total_score) + '" required></div>' +
                    '<div><label>及格分</label><input name="pass_score" type="number" min="0" value="' + esc(rule.pass_score) + '" required></div>' +
                    '<div><label>考试时长（分钟）</label><input name="duration_minutes" type="number" min="1" max="600" value="' + esc(rule.duration_minutes) + '" required></div>' +
                '</div>' +
                '<label>规则说明</label><textarea name="description" rows="2">' + esc(rule.description) + '</textarea>' +
                '<div class="btn-row" style="margin-top:10px"><button type="submit" class="btn btn-sm">保存规则</button></div>' +
            '</form>';
        }).join('');
        openModal('考试规则配置',
            '<div class="notice">修改规则只影响之后新建或重新保存的题库；已发布题库保存的是创建时的规则快照，' +
                '如要同步请在题库编辑页重新保存。</div>' + (cards || '<div class="empty">暂无规则</div>'));
        Array.prototype.forEach.call(modalBody.querySelectorAll('[data-rule]'), function (form) {
            form.addEventListener('submit', async function (e) {
                e.preventDefault();
                var data = new FormData(form);
                var body = {};
                data.forEach(function (value, key) { body[key] = value; });
                body.vendor_slug = '';
                body.cert_path = '';
                try {
                    var out = await api('/exam-rules/' + form.getAttribute('data-rule'), jsonBody(body));
                    toast(out.message, 'ok');
                    closeModal();
                } catch (err) { toast(err.message, 'err'); }
            });
        });
    }

    // ---------------------------------------------------------------- 调度
    // 串行渲染 + 导航代次：快速连点菜单时，先发出的请求即使晚回来，
    // 也不会把后点的页面覆盖掉（历史 bug：点「修改密码」偶尔停在概览）。
    var navSeq = 0;
    var renderQueue = Promise.resolve();

    async function paint(key) {
        if (!Object.prototype.hasOwnProperty.call(SECTIONS, key)) {
            activateMenu('overview');
            view.innerHTML = '<div class="card"><div class="empty">' +
                '当前浏览器加载的管理端脚本不是最新版本，找不到「' + esc(key) + '」模块。<br>' +
                '请按 <b>Ctrl + F5</b>（macOS 为 <b>Cmd + Shift + R</b>）强制刷新后重试。</div></div>';
            return;
        }
        activateMenu(key);
        view.innerHTML = '<div class="loading">正在加载管理数据…</div>';
        try {
            if (key === 'overview') await renderOverview();
            else if (key === 'sets') await renderSets();
            else if (key === 'import') await renderBankImport();
            else if (key === 'intros') await renderIntros('');
            else if (key === 'users') await renderUsers();
            else if (key === 'password') await renderPassword();
            else if (key === 'records') await renderRecords();
            else if (key === 'categories') await renderCategories('');
            else if (key === 'questions') await renderQuestions('', '', 1);
            else if (key === 'practice') await renderPracticeBanks('', '');
            else if (key === 'rankings') await renderRankings('day');
        } catch (err) {
            view.innerHTML = '<div class="card"><div class="empty">加载失败：' + esc(err.message) + '</div></div>';
        }
    }

    function render() {
        var seq = ++navSeq;
        var key = currentSection();
        renderQueue = renderQueue.then(function () {
            // 排队期间用户已经切到别的模块，这一次就不必再渲染了
            if (seq !== navSeq) return null;
            return paint(key);
        });
        return renderQueue;
    }

    function goSection(key) {
        if (location.hash === '#' + key) {
            render();
            return;
        }
        // 交给 hashchange 统一渲染，避免同一导航被渲染两次
        location.hash = key;
    }

    document.getElementById('admin-menu').addEventListener('click', function (e) {
        var btn = e.target.closest('[data-section]');
        if (!btn) return;
        goSection(btn.getAttribute('data-section'));
    });
    view.addEventListener('click', function (e) {
        var go = e.target.closest('[data-go]');
        if (go) { goSection(go.getAttribute('data-go')); return; }
        var el = e.target.closest('[data-action]');
        if (!el) return;
        var action = el.getAttribute('data-action');
        var date = el.getAttribute('data-date');
        if (action === 'edit') openEditor(date).catch(function (err) { toast(err.message, 'err'); });
        if (action === 'delete') openDelete(date).catch(function (err) { toast(err.message, 'err'); });
        if (action === 'reload') render();
    });
    document.getElementById('modal-close').addEventListener('click', closeModal);
    modal.addEventListener('click', function (e) {
        if (e.target === modal || e.target.closest('[data-close]')) closeModal();
    });
    document.getElementById('logout-btn').addEventListener('click', async function () {
        try { await api('/logout', { method: 'POST' }); } catch (e) { /* 忽略 */ }
        location.href = '/admin/login';
    });
    window.addEventListener('hashchange', render);

    (async function boot() {
        try {
            var s = await api('/session');
            if (!s.data.logged_in) { location.href = '/admin/login'; return; }
            me = s.data.admin;
            document.getElementById('admin-name').textContent = s.data.admin.username;
            // replaceState 只补地址栏，不触发 hashchange，避免启动时渲染两遍
            if (!location.hash) history.replaceState(null, '', '#overview');
            await render();
        } catch (err) {
            view.innerHTML = '<div class="card"><div class="empty">初始化失败：' + esc(err.message) + '</div></div>';
        }
    })();
})();
