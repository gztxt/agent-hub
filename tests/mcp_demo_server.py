"""Agent Hub MCP 网关自检用 demo server（stdio）：echo + now 两个工具（mcp 2.x MCPServer）"""
from datetime import datetime

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("hub-demo")


@mcp.tool()
def echo(text: str) -> str:
    """原样返回文本，带前缀"""
    return f"echo> {text}"


@mcp.tool()
def now() -> str:
    """当前时间"""
    return datetime.now().isoformat()


if __name__ == "__main__":
    mcp.run(transport="stdio")
