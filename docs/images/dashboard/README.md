# Dashboard 页面功能截图

这些截图使用原版前端连接独立live BFF，后端为隔离的真实MemOS、Neo4j、Qdrant和MCP网关。

记忆内容为虚构验收数据；原生SDK写入用于核对分类与读取映射，调用日志来自实际MCP请求。分类截图验证读取映射，自动语义抽取另行验收。

| 文件 | 展示内容 |
| --- | --- |
| 01-memory-list.png | 记忆列表、分类计数与分页 |
| 02-event-memory.png | Context / InsightMemory事件视图 |
| 03-profile-memory.png | UserMemory用户记忆视图 |
| 04-memory-detail.png | 独立详情读取 |
| 05-audit-log.png | 写入、检索与画像读取的调用日志 |
| 06-log-detail.png | 原始请求正文 |
| 07-usage.png | 日期范围与用量趋势；此时UI显示3次，源日志/API共4次，画像读取未被原版页面计入 |
| 08-models.png | 实际初始化模型清单 |

本次运行观察到抽取LLM认证401，以及持续上游502时页面加载态不结束；对应验收仍未通过。其他前端缺项见PR中的验收限制。
