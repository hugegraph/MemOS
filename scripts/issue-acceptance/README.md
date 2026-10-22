# Issue #1 验收测试入口

测试分层、配置与证据边界见[`docs/testing.md`](../../docs/testing.md)。
公开代码不包含私有部署历史、真实密钥和原始审计数据。

```powershell
pnpm test:issue          # 37操作契约、隔离业务和安全回归；不是实时服务验收
pnpm test:issue:safety   # runner自测，非真实业务验收
pnpm test:issue:real     # 未提供manifest时零网络、BLOCKED；配置方式见real/README.md
pnpm test:issue:browser  # 需ISSUE_WEB_BASE和ISSUE_CHROME；否则BLOCKED
```

禁止把这组命令的结果与旧 `test:mock` / `test:acceptance` 混算为真实验收覆盖。
Node skip在验收记录中是BLOCKED，不是PASS。P2/P3和规范冲突另列，不冒充必达功能缺陷。
浏览器停服/写删/切换及实际网关/Prompt生效仍需要专用环境和交互步骤，不会自动破坏演示部署。
