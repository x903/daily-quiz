// 「阿里云认证」三级下拉菜单：仅增强触屏 / 键盘点击展开，
// 桌面端仍由 CSS 的 :hover / :focus-within 展开，不影响其它导航链接。
(function () {
  "use strict";

  var toggles = document.querySelectorAll("[data-nav-dropdown] > .nav-toggle");
  if (!toggles.length) return;

  function closeAll() {
    var opened = document.querySelectorAll("[data-nav-dropdown].open");
    Array.prototype.forEach.call(opened, function (el) {
      el.classList.remove("open");
    });
    Array.prototype.forEach.call(toggles, function (btn) {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  Array.prototype.forEach.call(toggles, function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var item = btn.parentNode;
      var willOpen = !item.classList.contains("open");
      // 同级只保留一个展开项，避免二级 / 三级菜单重叠
      Array.prototype.forEach.call(item.parentNode.children, function (el) {
        if (el !== item) el.classList.remove("open");
      });
      item.classList.toggle("open", willOpen);
      btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
    });
  });

  document.addEventListener("click", function (e) {
    if (!e.target.closest || !e.target.closest(".nav-dropdown")) closeAll();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAll();
  });
})();
