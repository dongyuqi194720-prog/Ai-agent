import sys
import os

sys.path.insert(
    0,
    os.path.expanduser("~")
)

from ai_agent.tool_loader import load_tools


class ToolRouter:


    def __init__(self):

        self.tools = {}

        self.load()



    def load(self):

        tools = load_tools()


        for tool in tools:

            self.tools[tool.name] = tool



    def list_tools(self):

        return list(
            self.tools.keys()
        )



    def call(
        self,
        name,
        args
    ):

        if name not in self.tools:

            return (
                "工具不存在:"
                + name
            )


        tool = self.tools[name]


        try:

            return tool.invoke(args)


        except Exception as e:

            return (
                "工具执行失败:"
                + str(e)
            )



if __name__ == "__main__":


    router = ToolRouter()


    print("="*40)

    print(
        "AI Agent Router"
    )

    print("="*40)


    print(
        "可用工具:"
    )


    for t in router.list_tools():

        print(
            "-",
            t
        )
