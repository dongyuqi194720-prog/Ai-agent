import sys
import os

sys.path.insert(
    0,
    os.path.expanduser("~")
)

from ai_agent.tool_loader import load_tools

def main():

    print("=" * 40)
    print("AI Agent 工具自检")
    print("=" * 40)


    try:

        tools = load_tools()

        print("\n发现工具数量:", len(tools))


        success = 0


        for tool in tools:

            print("\n工具:", tool.name)


            if hasattr(tool, "invoke"):

                print("  invoke: OK")
                success += 1

            else:

                print("  invoke: FAIL")


        print("\n" + "=" * 40)

        if success == len(tools):

            print(
                "全部工具正常:",
                success,
                "/",
                len(tools)
            )

        else:

            print(
                "存在异常:",
                success,
                "/",
                len(tools)
            )


        print("=" * 40)


    except Exception as e:

        print("工具系统错误:")
        print(e)



if __name__ == "__main__":

    main()
