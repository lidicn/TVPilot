# TVPilot MCP Server · 使用示例

## 1. 启动

```bash
# HTTP/SSE 模式（默认 0.0.0.0:8765，端点 /mcp）
python -m tvpilot.mcp
TVPILOT_MCP_PORT=9000 python -m tvpilot.mcp --host 0.0.0.0 --port 9000

# stdio 模式
python -m tvpilot.mcp --stdio
```

## 2. MCP 客户端接入（Claude Desktop / 任意 MCP agent）

```json
{
  "mcpServers": {
    "tvpilot-http": {
      "url": "http://<电视所在局域网IP>:8765/mcp"
    },
    "tvpilot-stdio": {
      "command": "python",
      "args": ["-m", "tvpilot.mcp", "--stdio"],
      "cwd": "E:/NAS/TVPilot"
    }
  }
}
```

## 3. 裸协议调试（curl）

```bash
# 握手
curl -X POST http://127.0.0.1:8765/mcp -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}'

# 工具清单（<=10 个，描述 <=80 字符）
curl -X POST http://127.0.0.1:8765/mcp -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# 换台 —— 直接回业务字段，无 ok/cost_ms 包装
curl -X POST http://127.0.0.1:8765/mcp -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"tv_zap","arguments":{"channel":"CCTV1"}}}'
# => {"channel":"中央一台","channel_no":1,...}

# 截图 —— 只回 URL，默认 240p（宽 426），响应不含 base64
curl -X POST http://127.0.0.1:8765/mcp -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"tv_screenshot"}}'
# => {"url":"http://192.168.x.x:PORT/shot_xxx.png","width":426,"height":240}

# 蓝图：一次调用跑完整流程
curl -X POST http://127.0.0.1:8765/mcp -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"tv_blueprint_run","arguments":{"name":"zap_to","params":{"channel":"CCTV1"}}}}'
```

stdio 模式同样协议，换行分隔：每行一个 JSON-RPC 请求，每行一个响应。

## 4. Agent 侧收益（省 token）

- 换台/回桌面/开设置等复合流程收敛为 `tv_blueprint_run` 一次调用，
  steps 只回 `{step, ok, status, error}`，丢弃 output 回显。
- 截图走 URL：agent 需要看图时才取图，文本上下文零图片字节。
- 所有 content 为紧凑 JSON（无空格、中文不转义 \uXXXX）。

## 5. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| TVPILOT_MCP_HOST / PORT | 0.0.0.0 / 8765 | HTTP 监听 |
| TVPILOT_MCP_SHOT_PORT | 0（随机） | 截图静态服务端口 |
| TVPILOT_MCP_BASE_URL | 空 | 手动指定截图 URL 前缀（反代/固定域名时用） |
| TVPILOT_MCP_SHOT_DIR | 系统临时目录/tvpilot_shots | 截图落盘目录 |
