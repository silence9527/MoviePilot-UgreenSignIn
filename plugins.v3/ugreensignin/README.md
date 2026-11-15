# 绿联论坛签到使用说明

## 1. 获取 Cookie

1. 在浏览器打开并登录 <https://club.ugnas.com/>。
2. 按 `F12` 打开开发者工具，进入 `Network`。
3. 刷新页面，选择 `club.ugnas.com` 的文档请求。
4. 在 `Request Headers` 中找到 `Cookie`，复制完整值。

Cookie 必须包含名称以 `_auth` 结尾的登录凭证。常见形式为 `6LQh_2132_auth=...`，前缀可能随论坛调整。

不要复制 `Cookie:` 以外的其他请求头，也不要把 Cookie 发到公开 issue 或提交到仓库。

## 2. 配置插件

| 配置项 | 建议值 | 说明 |
| --- | --- | --- |
| 启用插件 | 开启 | 开启后才注册定时任务 |
| 发送通知 | 开启 | 成功、已签到和失败均发送通知 |
| 论坛 Cookie | 浏览器复制的完整值 | 只保存在本机 MoviePilot 配置中 |
| 签到周期 | `0 8 * * *` | 每天 08:00，使用五位 Cron |
| 浏览器超时秒数 | `90` | 网络较慢时可调至 `120` |
| 立即运行一次 | 首次配置时开启 | 保存后约 3 秒执行，开关自动复位 |

## 3. 验证结果

打开插件详情页：

- `签到成功`：本次点击后，刷新页面确认今日已签到。
- `今日已签到`：运行前已经完成，不重复点击。
- `失败`：根据提示更新 Cookie、检查网络或调整超时。

签到完成后插件会再读一次 `home.php?mod=spacecp&ac=credit`，取当前积分余额与今日 `每日签到` 的积分变化，写入通知和记录。读不到时只省略这几行，不影响签到判定。

插件最多保存 90 条历史，页面展示最近 30 条，并在积分列显示本次变化与余额。

## 注意事项

- 本插件不自动获取浏览器 Cookie，也不保存论坛账号密码。
- Cookie 过期后必须重新复制，插件不会把登录页误判为签到成功。
- MoviePilot 需要具备 V3 浏览器运行能力。插件通过 `app.sdk.browser.launch_browser_context` 使用宿主浏览器。
- 签到页面为 <https://club.ugnas.com/plugin.php?id=ugreen_checkin>。

## 实现依据

- [MoviePilot V3 插件开发指南](https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/Plugin_Development.md)
- [MoviePilot 插件 Wiki](https://wiki.movie-pilot.org/plugin)
- 2026-11-16 实测：签到前显示 `立即签到`，完成后刷新显示 `今日已签到`。
