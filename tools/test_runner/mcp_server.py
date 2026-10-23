import asyncio
from tools.test_runner.tool import TestRunnerTool


def main():
    tool = TestRunnerTool()
    server = tool.get_mcp_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
