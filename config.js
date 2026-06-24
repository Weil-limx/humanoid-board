// 榜单数据源配置。
// 默认读同站静态文件（由 Worker push 到 GitHub Pages 仓库 data/ 下）；
// 联调时可改为后端直连。生产读路径走静态文件（https 页面直连 http 后端会被混合内容策略拦截）。
window.BOARD_CONFIG = {
  BOARD_DATA_URL: "./data/leaderboard.json",
  REFRESH_SECONDS: 60,
};
