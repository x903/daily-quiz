/* 配置驱动的三级栏目选择器：题目导入页与题库编辑页共用。
   栏目树由模板通过 data-tree 注入（navigation.category_tree_payload()），
   因此新增认证体系或 ACP 方向时无需修改本脚本。 */
(function () {
    'use strict';

    var LEVEL_HINTS = ['请选择一级栏目', '请选择二级栏目', '请选择三级栏目'];

    function findPath(nodes, target, acc) {
        for (var i = 0; i < nodes.length; i++) {
            var next = acc.concat([nodes[i]]);
            if (nodes[i].slug === target) return next;
            var hit = findPath(nodes[i].children || [], target, next);
            if (hit) return hit;
        }
        return null;
    }

    function bySlug(nodes, slug) {
        for (var i = 0; i < nodes.length; i++) {
            if (nodes[i].slug === slug) return nodes[i];
        }
        return null;
    }

    function init(root) {
        var tree;
        try {
            tree = JSON.parse(root.getAttribute('data-tree') || '[]');
        } catch (e) {
            return;
        }
        var selects = Array.prototype.slice.call(root.querySelectorAll('[data-cat-level]'));
        var hidden = root.querySelector('[data-category-value]');
        var hint = root.querySelector('[data-category-hint]');
        var selected = root.getAttribute('data-selected') || '';
        var required = root.getAttribute('data-required') === '1';
        if (!selects.length || !hidden) return;

        var path = selected ? (findPath(tree, selected, []) || []) : [];
        // 只有一个一级栏目时自动选中它，用户只需再选二级 / 三级
        if (!path.length && tree.length === 1) path = [tree[0]];

        function optionsFor(level) {
            if (level === 0) return tree;
            var parent = path[level - 1];
            return parent ? (parent.children || []) : [];
        }

        function render() {
            var incomplete = false;
            for (var i = 0; i < selects.length; i++) {
                var nodes = optionsFor(i);
                var sel = selects[i];
                var current = path[i] ? path[i].slug : '';

                sel.innerHTML = '';
                var placeholder = document.createElement('option');
                placeholder.value = '';
                placeholder.textContent = nodes.length ? LEVEL_HINTS[i] : '无下级栏目';
                sel.appendChild(placeholder);
                nodes.forEach(function (n) {
                    var opt = document.createElement('option');
                    opt.value = n.slug;
                    opt.textContent = n.label;
                    sel.appendChild(opt);
                });

                // 某级只有一个可选项时自动选中，避免多余点击
                if (!current && nodes.length === 1) current = nodes[0].slug;
                if (current && !path[i]) path[i] = bySlug(nodes, current);
                sel.value = current || '';
                sel.disabled = nodes.length === 0;
                sel.classList.toggle('is-hidden', incomplete || nodes.length === 0);

                if (!current) {
                    incomplete = true;
                    for (var j = i + 1; j < selects.length; j++) {
                        selects[j].value = '';
                        selects[j].disabled = true;
                        selects[j].classList.add('is-hidden');
                    }
                    break;
                }
            }

            var last = path[path.length - 1];
            var isLeaf = !!last && !(last.children || []).length;
            hidden.value = isLeaf ? last.slug : '';
            if (isLeaf) {
                hint.textContent = '当前栏目：' + path.map(function (n) {
                    return n.label;
                }).join(' › ');
                hint.classList.remove('is-error');
            } else {
                hint.textContent = required
                    ? '请逐级选择到最末一级栏目后再导入'
                    : '如需调整所属栏目，请逐级选择；保持不选则不修改';
            }
        }

        selects.forEach(function (sel, level) {
            sel.addEventListener('change', function () {
                path = path.slice(0, level);
                var chosen = bySlug(optionsFor(level), sel.value);
                if (chosen) path.push(chosen);
                render();
            });
        });

        // hidden 输入框无法用 required 校验，导入页在提交前显式拦截
        var form = root.closest('form');
        if (form && required) {
            form.addEventListener('submit', function (e) {
                if (!hidden.value) {
                    e.preventDefault();
                    hint.textContent = '请先选择完整的导入栏目';
                    hint.classList.add('is-error');
                }
            });
        }
        render();
    }

    Array.prototype.forEach.call(
        document.querySelectorAll('[data-category-picker]'), init
    );
})();
