# MoviePilot 绿联论坛签到

适用于 MoviePilot V3 的绿联 NAS 社区每日签到插件。

插件使用论坛当前的 `ugreen_checkin` 独立签到页。点击后重新加载页面确认状态，不把请求发出或按钮变化直接当作成功。

## 功能

- 每天定时自动签到
- 保存配置后可立即运行一次
- 自动识别今日已签到，不重复提交
- 明确区分 Cookie 失效、页面变化和签到失败
- 支持 MoviePilot 通知与最近 30 条记录展示
- 不保存账号密码，不在日志和详情页展示 Cookie

## 安装

1. 打开 MoviePilot 插件市场设置。
2. 添加插件仓库：

   ```text
   https://github.com/silence9527/MoviePilot-UgreenSignIn
   ```

3. 在插件市场搜索 `绿联论坛签到` 并安装。
4. 按下方说明配置 Cookie，启用插件。

详细配置见[插件使用说明](plugins.v3/ugreensignin/README.md)。

## 目录

```text
plugins.v3/ugreensignin/
├── __init__.py
├── client.py
└── README.md
package.v3.json
icons/ugreen.svg
tests/v3/ugreensignin/test_client.py
```

## 验证

```bash
python -m compileall plugins.v3/ugreensignin
python -m unittest discover -s tests/v3/ugreensignin -v
```

插件浏览器流程已在 2026-11-16 的绿联社区页面实际完成签到并复核。MoviePilot 宿主加载仍应以目标实例的安装结果为准。

## 许可

MIT
