import sys
import os

sys.path.insert(
    0,
    os.path.expanduser("~")
)

from ai_agent.tool_loader import load_tools


def build_registry():

    tools = load_tools()

    registry = []


    for tool in tools:

        item = {
            "name": tool.name,
            "description": tool.description
        }

        registry.append(item)


    return registry



def print_registry():

    registry = build_registry()


    print("=" * 40)
    print("AI Agent 工具注册表")
    print("=" * 40)


    for i, tool in enumerate(
        registry,
        start=1
    ):

        print()

        print(
            f"{i}. {tool['name']}"
        )

        print(
            "说明:",
            tool["description"]
        )


    print()

    print(
        "工具总数:",
        len(registry)
    )



if __name__ == "__main__":

    print_registry()
