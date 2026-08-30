import os


from langchain_openai import ChatOpenAI


from ai_agent.router import ToolRouter


from ai_agent.v3.autonomous_agent import AutonomousAgent
from codex_bridge import CodexBridge



MODEL = os.environ.get(
    "AI_MODEL",
    "qwen2.5"
)


BASE_URL = os.environ.get(
    "AI_BASE_URL",
    "http://127.0.0.1:8080/v1"
)



llm = ChatOpenAI(

    model=MODEL,

    base_url=BASE_URL,

    api_key="none",

    temperature=0.2

)



router = ToolRouter()

codex = CodexBridge(
    model="gpt-5.6-terra"
)



print()

print(
    "================================"
)

print(
    " AI Programmer V3"
)

print(
    "================================"
)

print()

print(
    "模型:",
    MODEL
)

print(
    "地址:",
    BASE_URL
)

print()


print(
    "工具:"
)


for tool in router.list_tools():

    print(
        "-",
        tool
    )


print()

print(
    "输入 exit 退出"
)



agent = AutonomousAgent(

    llm,

    router,

    project=os.path.expanduser(
        "~/llama.cpp"
    )

)



while True:


    try:

        question = input(
            "\n你: "
        ).strip()


    except KeyboardInterrupt:

        print()

        break


    except EOFError:

        break



    if not question:

        continue



    if question == "exit":

        break



    agent.run(
        question
    )
