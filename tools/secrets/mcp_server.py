import asyncio
from tools.secrets.tool import SecretsTool


def main():
    tool = SecretsTool()
    server = tool.get_mcp_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
