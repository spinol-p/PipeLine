import asyncio
from tools.contracts.tool import ContractsTool


def main():
    tool = ContractsTool()
    server = tool.get_mcp_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
