import asyncio
from tools.deps.tool import DepsTool


def main():
    tool = DepsTool()
    server = tool.get_mcp_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
