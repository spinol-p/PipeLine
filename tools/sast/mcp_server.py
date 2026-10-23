import asyncio
from tools.sast.tool import SastTool


def main():
    tool = SastTool()
    server = tool.get_mcp_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
