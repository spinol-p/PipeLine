import asyncio
from tools.dockerfile.tool import DockerfileTool


def main():
    tool = DockerfileTool()
    server = tool.get_mcp_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
