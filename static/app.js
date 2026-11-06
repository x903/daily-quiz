// 每日答题系统前端逻辑：计时、进度、当前题指示、提交保护
(function () {
  "use strict";

  var startTime = Date.now();
  var durationInput = document.getElementById("duration");
  var timerEl = document.getElementById("timer");
  var progressBar = document.getElementById("progress-bar");
  var progressText = document.getElementById("progress-text");
  var totalEl = document.getElementById("total-count");
  var currentSetEl = document.getElementById("current-set");
  var form = document.getElementById("quiz-form");

  var TOTAL = totalEl ? parseInt(totalEl.value, 10) : 0;

  // 计时器
  function tick() {
    if (!timerEl) return;
    var sec = Math.floor((Date.now() - startTime) / 1000);
    var m = String(Math.floor(sec / 60)).padStart(2, "0");
    var s = String(sec % 60).padStart(2, "0");
    timerEl.textContent = m + ":" + s;
  }
  if (timerEl) {
    tick();
    setInterval(tick, 1000);
  }

  // 进度统计
  function updateProgress() {
    if (!progressBar) return;
    var answered = document.querySelectorAll(
      'input[type=radio]:checked, input[type=checkbox]:checked'
    ).length;
    var pct = TOTAL ? Math.round((answered / TOTAL) * 100) : 0;
    progressBar.style.width = pct + "%";
    if (progressText) progressText.textContent = answered + " / " + TOTAL;
  }
  if (form) {
    document.addEventListener("change", updateProgress);
    updateProgress();
  }

  // 当前题指示（滚动时高亮）
  var setEls = Array.prototype.slice.call(document.querySelectorAll(".quiz-set"));
  if (setEls.length && currentSetEl && "IntersectionObserver" in window) {
    var obs = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) {
            currentSetEl.textContent = e.target.getAttribute("data-set");
          }
        });
      },
      { rootMargin: "-40% 0px -55% 0px" }
    );
    setEls.forEach(function (el) { obs.observe(el); });
  }

  // 提交：写入用时 + 未答提示
  if (form) {
    form.addEventListener("submit", function () {
      if (durationInput) {
        durationInput.value = Math.floor((Date.now() - startTime) / 1000);
      }
    });

    var submitBtn = document.getElementById("submit-btn");
    if (submitBtn) {
      submitBtn.addEventListener("click", function (e) {
        if (!TOTAL) return;
        var answered = document.querySelectorAll(
          'input[type=radio]:checked, input[type=checkbox]:checked'
        ).length;
        if (answered < TOTAL) {
          if (!confirm("还有 " + (TOTAL - answered) + " 题未作答，确定提交吗？")) {
            e.preventDefault();
          }
        }
      });
    }
  }
})();
