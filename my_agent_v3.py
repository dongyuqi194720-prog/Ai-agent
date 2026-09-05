import os

# V6：统一代理协议，httpx 不接受 socks://，统一转换为 socks5://。
for _proxy_key in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    _proxy = os.environ.get(_proxy_key)
    if _proxy and _proxy.startswith("socks://"):
        os.environ[_proxy_key] = "socks5://" + _proxy[len("socks://"):]



from langchain_openai import ChatOpenAI


from ai_agent.router import ToolRouter


from v3.autonomous_agent import AutonomousAgent
from codex_bridge import CodexBridge



MODEL = os.environ.get(
    "AI_MODEL",
    "qwen2.5"
)


BASE_URL = os.environ.get(
    "AI_BASE_URL",
    "http://127.0.0.1:8080/v1"
)

# V6.7: Decision Layer 后端选择。
# 默认 local，保持 V6.6 / 本地 Qwen 行为不变。
DECISION_MODE = os.environ.get(
    "AI_DECISION_MODE",
    "local"
).strip().lower()



# V6：本地 Qwen 使用 127.0.0.1，不经过 SOCKS 代理。
_proxy_env = {}
for _proxy_key in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    if _proxy_key in os.environ:
        _proxy_env[_proxy_key] = os.environ.pop(_proxy_key)

try:
    llm = ChatOpenAI(
        model=MODEL,
        base_url=BASE_URL,
        api_key="none",
        temperature=0.2
    )
finally:
    os.environ.update(_proxy_env)


# V6.7: Remote GPT 只作为独立 Decision Layer。
# local 模式完全保持现有本地 Qwen。
decision_llm = None

if DECISION_MODE == "remote":

    remote_api_key = os.environ.get(
        "OPENAI_API_KEY",
        ""
    ).strip()

    if not remote_api_key:
        raise RuntimeError(
            "AI_DECISION_MODE=remote 但 OPENAI_API_KEY 未设置"
        )

    decision_llm = ChatOpenAI(
        model=os.environ.get(
            "AI_DECISION_MODEL",
            "gpt-5.6"
        ),
        api_key=remote_api_key,
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
    ),

    decision_llm=decision_llm

)



while True:


    try:

        print("\n你: ", end="", flush=True)
        input_lines = []

        while True:
            line = input()

            if line.strip() == "END":
                break

            input_lines.append(line)

        question = "\n".join(
            input_lines
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



    result = agent.run(
        question
    )

    # V6.8：普通 Codex 聊天人工接力。
    if isinstance(result, dict) and result.get("waiting_codex"):
        print()
        print("========== 普通 Codex 请求 ==========")
        print(result.get("codex_request", ""))
        print("========== END CODEX REQUEST ==========")
        print()
        print("请把普通 Codex 的回复粘贴到下面：")

        codex_response = input().strip()

        if codex_response:
            print("V6.8: 收到 Codex 回复，正在恢复...")
            result = agent.resume_after_codex(
                codex_response
            )
            print("V6.8: Codex 恢复完成")

