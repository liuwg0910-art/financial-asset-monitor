# API参考

后端默认9090端口，交互文档位于`/docs`，OpenAPI位于`/openapi.json`。这里记录当前接口用途，精确校验约束以运行中的OpenAPI与源码为准。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 健康状态、时间、自选数量 |
| GET | `/api/search?q=螺纹钢` | 名称/代码搜索及候选 |
| GET | `/api/custom` | 自选列表 |
| POST | `/api/custom` | 验证并添加，JSON为`symbol`及可选`name` |
| DELETE | `/api/custom/{symbol}` | 删除自选及对应普通波动规则 |
| GET | `/api/prices` | 自选行情 |
| GET | `/api/prices/{symbol}` | 单个报价 |
| GET | `/api/kline/{symbol}?interval=1h&limit=100` | OHLCV、来源、支持周期及说明 |
| GET | `/api/global-markets` | 独立全球市场基准 |
| GET | `/api/basis` | IC / IM两腿报价、同步/过时状态、年化贴水 |
| GET | `/api/basis/history` | 近一年历史点、缺口、回退与重试状态 |
| GET | `/api/alerts` | 普通规则、贴水规则、事件与脱敏通知配置 |
| POST | `/api/alerts/rules` | 添加普通波动规则 |
| PATCH | `/api/alerts/rules/{id}` | `enabled`暂停/启用 |
| DELETE | `/api/alerts/rules/{id}` | 删除普通规则 |
| POST | `/api/alerts/basis-rules` | 添加带符号贴水阈值 |
| PATCH | `/api/alerts/basis-rules/{id}` | `enabled`暂停/启用 |
| DELETE | `/api/alerts/basis-rules/{id}` | 删除贴水规则 |
| POST | `/api/alerts/wechat` | `enabled`及可选`sendKey`保存微信配置 |
| POST | `/api/alerts/wechat/test` | 实际向已配置通道发送测试通知 |
| WebSocket | `/ws` | 价格、告警及投递状态推送 |

普通规则请求示例（标的需先加入自选）：

```json
{"symbol":"RB2701","windowMinutes":5,"thresholdPct":1,"direction":"both","cooldownMinutes":5}
```

贴水阈值示例（无需加入自选）：

```json
{"symbol":"IC","operator":"lte","thresholdPct":-10,"cooldownMinutes":60}
```

`lte`为小于等于，`gte`为大于等于。波动规则方向为`both/up/down`。规则仅添加后启用，不根据示例自动创建。

动态代码作为URL路径时请编码，例如`BZ=F`使用`encodeURIComponent`。报价的`updated`不统一代表请求时刻：期货使用源报价时间，并可带`quoteTime/receivedAt`。请根据响应元数据和页面说明判断新鲜度。

常见错误包括400（输入/行情验证失败）、404（不存在或无有效历史）、409（重复自选）、422（字段或周期不支持）、502（行情源失败）。部分聚合接口会返回200及单项不可用/快照状态，不要仅凭HTTP状态认定实时有效。

接口没有账户认证，不能作为公开第三方服务直接暴露。微信测试接口会产生真实外部请求，调用前确认通道归属与服务额度。
