// 「认证题库网」三级下拉菜单交互：
//   1) 桌面端：CSS :hover 负责展开；这里额外做 260ms 的延迟关闭与 hover-intent，
//      避免鼠标从一级/二级标题斜向下移动到面板时，穿过间隙被瞬间收起。
//   2) 触屏 / 键盘：点击 ▾ 展开对应层级（保留原有行为）。
(function () {
  "use strict";

  var items = document.querySelectorAll(
    "[data-nav-dropdown], .nav-submenu .has-children"
  );
  var toggles = document.querySelectorAll(
    "[data-nav-dropdown] > .nav-toggle, .nav-submenu .has-children > .nav-toggle"
  );
  if (!items.length || !toggles.length) return;

  var CLOSE_DELAY = 260;
  var canHover = !window.matchMedia || window.matchMedia("(hover: hover)").matches;

  function ownToggle(item) {
    for (var i = 0; i < item.children.length; i += 1) {
      if (item.children[i].classList.contains("nav-toggle")) return item.children[i];
    }
    return null;
  }

  function setOpen(item, open) {
    item.classList.toggle("open", open);
    var btn = ownToggle(item);
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function cancelClose(item) {
    if (item._navCloseTimer) {
      clearTimeout(item._navCloseTimer);
      item._navCloseTimer = null;
    }
  }

  function closeLater(item) {
    cancelClose(item);
    item._navCloseTimer = setTimeout(function () {
      item._navCloseTimer = null;
      setOpen(item, false);
    }, CLOSE_DELAY);
  }

  // 同级只保留一个展开项，避免两个面板同时挂着、互相遮挡
  function closeSiblings(item) {
    var parent = item.parentNode;
    if (!parent) return;
    Array.prototype.forEach.call(parent.children, function (el) {
      if (el === item || !el.classList) return;
      cancelClose(el);
      if (el.classList.contains("open")) setOpen(el, false);
    });
  }

  function closeAll() {
    Array.prototype.forEach.call(items, function (el) {
      cancelClose(el);
      el.classList.remove("open");
    });
    Array.prototype.forEach.call(toggles, function (btn) {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  if (canHover) {
    Array.prototype.forEach.call(items, function (item) {
      item.addEventListener("mouseenter", function () {
        cancelClose(item);
        closeSiblings(item);
        // 悬停即置为 open，这样即便指针中途离开触发层的盒子，
        // 面板也不会在下一帧被 display:none 抹掉
        setOpen(item, true);
      });
      item.addEventListener("mouseleave", function () {
        closeLater(item);
      });
    });
  }

  Array.prototype.forEach.call(toggles, function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var item = btn.parentNode;
      // 鼠标正悬停时展开态由 hover 维持，点击只保证“展开”，避免误关
      if (canHover && item.matches && item.matches(":hover")) {
        cancelClose(item);
        setOpen(item, true);
        return;
      }
      var willOpen = !item.classList.contains("open");
      closeSiblings(item);
      cancelClose(item);
      setOpen(item, willOpen);
    });
  });

  document.addEventListener("click", function (e) {
    if (!e.target.closest || !e.target.closest(".nav-dropdown")) closeAll();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAll();
  });

  // 前进/后退时浏览器可能从往返缓存恢复页面，顺手清掉残留的展开态
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) closeAll();
  });
})();
