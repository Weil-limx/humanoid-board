// Humanoid Locomotion (Task F / Oli EDU 跨箱子) 榜单渲染。
// 固定箱：满分 = 到达 60 + 跨越奖励(cross 10 / feet_land 5 / crouch 10，最高 +25) − 接触罚分。
// 字段对齐后端 scoring.py breakdown 键：reach / cross_bonus / feet_land_bonus / crouch_bonus /
//   torso_penalty / leg_penalty / finished / total。（move_bonus 在固定箱下恒 0，不展示。）
(function () {
  'use strict';

  var cfg = window.BOARD_CONFIG || {};
  var URL = cfg.BOARD_DATA_URL || './data/leaderboard.json';
  var REFRESH = (cfg.REFRESH_SECONDS || 60) * 1000;
  var BOARD = 'dev';

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function fmt(n, d) {
    if (n === null || n === undefined) return '—';
    return Number(n).toFixed(d === undefined ? 1 : d);
  }

  // 通过方式：reach 到了但无跨越奖励 = 绕行；有任一跨越奖励 = 跨越。
  function methodTag(b) {
    var crossed = (b.cross_bonus || 0) > 0;
    if (crossed) return '<span class="vchip">跨越</span>';
    if ((b.reach || 0) > 0) return '<span class="vchip">绕行</span>';
    return '<span class="dimcell">—</span>';
  }

  // 跨越奖励 = cross_bonus + feet_land_bonus + crouch_bonus（固定箱下最高 +25）。
  function crossCell(b) {
    var c = (b.cross_bonus || 0) + (b.feet_land_bonus || 0) + (b.crouch_bonus || 0);
    if (!c) return '<span class="gate gate-miss" title="无跨越奖励">0</span>';
    var parts = [];
    if (b.cross_bonus) parts.push('跨越');
    if (b.feet_land_bonus) parts.push('落地');
    if (b.crouch_bonus) parts.push('降重心');
    return '<span class="gate gate-ok" title="' + parts.join('+') + '">+' + fmt(c, 0) + '</span>';
  }

  function totalCell(r) {
    var b = r.breakdown || {};
    if (r.total === null || r.total === undefined) {
      return '<td class="c-t3"><span class="dimcell">未上场</span></td>';
    }
    var w = Math.max(2, Math.min(100, r.total));
    var reached = (b.reach || 0) > 0 ? '到达终点' : '未到达';
    var pen = (b.torso_penalty || 0) + (b.leg_penalty || 0);
    var sub = '<span class="t3sub">' + reached + (pen < 0 ? ' · 罚 ' + fmt(pen, 0) : '') + '</span>';
    return '<td class="c-t3"><div class="t3wrap">' +
      '<span class="t3num">' + fmt(r.total) + '</span>' + sub +
      '<span class="t3bar"><i style="width:' + w + '%"></i></span></div></td>';
  }

  function rowHtml(r) {
    var cls = r.rank <= 3 ? ' top top' + r.rank : '';
    var b = r.breakdown || {};
    return '<tr class="brow' + cls + '">' +
      '<td class="c-rank">' + r.rank + '</td>' +
      '<td class="c-team">' + esc(r.team) + '</td>' +
      '<td class="c-gate">' + methodTag(b) + '</td>' +
      '<td class="c-gate">' + crossCell(b) + '</td>' +
      totalCell(r) +
      '</tr>';
  }

  var countdownTimer = null;

  function renderCountdown(deadline) {
    var el = document.getElementById('countdown');
    if (!el) return false;
    if (!deadline) { el.textContent = '—'; return false; }
    var end = new Date(deadline).getTime();
    function tick() {
      var ms = end - Date.now();
      if (ms <= 0) {
        el.textContent = '已截止 · 榜单已冻结为最终成绩';
        el.classList.add('over');
        return;
      }
      var s = Math.floor(ms / 1000);
      var d = Math.floor(s / 86400);
      var pad = function (n) { return String(n).padStart(2, '0'); };
      el.textContent = '距截止 ' + d + ' 天 ' + pad(Math.floor(s % 86400 / 3600)) +
        ':' + pad(Math.floor(s % 3600 / 60)) + ':' + pad(s % 60);
    }
    if (countdownTimer) clearInterval(countdownTimer);
    tick();
    countdownTimer = setInterval(tick, 1000);
    return end - Date.now() <= 0;
  }

  function render(data) {
    var updated = document.getElementById('updated');
    if (updated) updated.textContent = '更新于 ' + (data.generated_at || '—');
    var over = renderCountdown(data.deadline);

    var locked = document.getElementById('locked');
    var table = document.getElementById('board-table');
    var empty = document.getElementById('empty');

    var rows;
    if (BOARD === 'final') {
      if (data.final_unlocked && data.final && data.final.length) {
        rows = data.final;
      } else if (over) {
        rows = data.dev || [];
      } else {
        if (locked) locked.hidden = false;
        if (table) table.hidden = true;
        if (empty) empty.hidden = true;
        return;
      }
    }
    if (locked) locked.hidden = true;

    rows = rows || data[BOARD] || [];
    if (!rows.length) {
      if (table) table.hidden = true;
      if (empty) empty.hidden = false;
      return;
    }
    table.hidden = false;
    if (empty) empty.hidden = true;
    table.querySelector('tbody').innerHTML = rows.map(rowHtml).join('');
  }

  function load() {
    fetch(URL + '?t=' + Date.now())
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { /* 静态站：加载失败保持现状，下轮重试 */ });
  }

  window.addEventListener('DOMContentLoaded', function () {
    BOARD = document.body.dataset.board || 'dev';
    load();
    setInterval(load, REFRESH);
  });
})();
