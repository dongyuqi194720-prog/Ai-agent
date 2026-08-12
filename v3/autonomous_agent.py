import json


class AutonomousAgent:


    def __init__(
        self,
        llm,
        router,
        project=None
    ):

        self.llm = llm
        self.router = router
        self.project = project
        self.max_steps = 20
        self.state = {
            "phase": "SEARCH",
            "search_done": False,
            "target_file": None,
            "read_done": False,
            "analyze_done": False,
            "tool_failures": 0
        }
         

        from .memory import Memory
        from .validator import Validator
        from .controller import Controller

        self.memory = Memory()

        self.validator = Validator()

        self.controller = Controller(
            router,
            self.memory,
            self.validator
        )



    def ask_llm(
        self,
        prompt
    ):

        response = self.llm.invoke(
            prompt
        )

        if hasattr(
            response,
            "content"
        ):
            return response.content

        return str(response)



    def extract_tool(
        self,
        text
    ):

        import re


        m = re.search(
            r"<search_code_index>\s*(.*?)\s*</search_code_index>",
            text,
            re.S
        )

        if m:

            args = m.group(1).strip()

            if "server_queue" in args:

                args = "server_queue"

            else:

                args = args.split("\n")[0].strip()


            return (
                "search_code_index",
                args
            )

        m = re.search(
            r"<read_file_chunk>\s*(.*?)\s*</read_file_chunk>",
            text,
            re.S
        )

        if m:
            return (
                "read_file_chunk",
                m.group(1).strip()
            )


        m = re.search(
            r"<analyze_code>\s*(.*?)\s*</analyze_code>",
            text,
            re.S
        )

        if m:
            return (
                "analyze_code",
                m.group(1).strip()
            )


        return None,None

    def allow_tool(self, tool):

        phase = self.state["phase"]


        if phase == "SEARCH":

            return tool == "search_code_index"


        if phase == "READ":

            return tool == "read_file_chunk"


        if phase == "ANALYZE":

            return tool == "analyze_code"


        if phase == "SUMMARY":

            return False


        return False

    def allow_tool(self, tool):

        phase = self.state["phase"]

        rules = {

            "SEARCH": [
                "search_code_index"
            ],

            "READ": [
                "read_file_chunk"
            ],

            "ANALYZE": [
                "analyze_code"
            ],

            "SUMMARY": []

        }

        return tool in rules.get(
            phase,
            []
        )

    def build_prompt(
        self,
        question,
        observation
    ):

        return f"""

重要规则:

一次回复只能调用一个工具。

禁止同时输出多个:
<search_code_index>
<read_file_chunk>
<analyze_code>

完成一个工具后，
等待下一轮。
当前阶段:
{self.state["phase"]}

已完成:
搜索={self.state["search_done"]}
读取={self.state["read_done"]}
分析={self.state["analyze_done"]}
你是 AI Programmer 自主执行 Agent。

项目:
{self.project}


用户任务:
{question}


当前工具结果:
{observation}


执行规则:

1. 不能询问用户。

2. 必须自主完成任务。

3. 分析源码流程:

search_code_index
 ->
read_file_chunk
 ->
analyze_code


4. 搜索必须使用:

<search_code_index>
关键词
</search_code_index>


5. 读取源码必须使用:

<read_file_chunk>
文件路径|开始行|数量
</read_file_chunk>


6. 禁止:
- grep
- find
- shell搜索


如果已经获得文件路径，
禁止再次搜索。


继续执行下一步。
5. read_file_chunk 的文件路径必须来自 search_code_index 返回的 file 字段。

6. 禁止读取项目目录，例如:
   /home/baixin/llama.cpp

7. 如果搜索结果包含:
   /tools/server/server-queue.cpp

   必须读取该文件。
"""

    def run(
        self,
        question
    ):

        print()

        print("================================")
        print("项目:", self.project)
        print("任务:", question)

        observation = ""

        for step in range(
            1,
            self.max_steps + 1
        ):

            print()
            print(
                "========== 自主执行",
                step,
                "=========="
            )


            prompt = self.build_prompt(
                question,
                observation
            )


            response = self.ask_llm(
                prompt
            )


            print(response)


            tool, args = self.extract_tool(
                response
            )


            if not tool:

                print(
                    "分析完成"
                )

                break

            if not self.allow_tool(tool):

                print(
                    "当前阶段禁止执行:",
                    tool,
                    "当前阶段:",
                    self.state["phase"]
                )

                continue

            if not self.allow_tool(tool):

                print(
                    "当前阶段禁止执行:",
                    tool,
                    "当前阶段:",
                    self.state["phase"]
                )

                self.state["phase"] = "SUMMARY"

                continue

            print(
                "执行工具:",
                tool
            )


            print(
                "参数:",
                args
            )

            if (
                tool == "read_file_chunk"
                and self.state["read_done"]
            ):

                print("源码已经读取，跳过重复读取")

                continue


            result = self.controller.call(
                tool,
                args
            )

            if tool == "read_file_chunk":

                if "这是目录" not in result:

                    self.state["read_done"] = True
                    self.state["phase"] = "ANALYZE"

            if tool == "analyze_code":

                self.state["analyze_done"] = True
                self.state["phase"] = "SUMMARY"

            if tool == "search_code_index":

                self.state["search_done"] = True

                try:
                    import json

                    if isinstance(result, str):

                        data = json.loads(result)

                    else:

                        data = result


                    for item in data:

                        file_path = item.get("file", "")

                        if "server-queue.cpp" in file_path:

                            self.state["target_file"] = file_path

                            print(
                                "锁定目标文件:",
                                file_path
                            )

                            break


                except Exception as e:

                    print(
                        "解析搜索结果失败:",
                        e
                    )


            # 搜索完成后，把结果交给模型继续决定下一步
            if tool == "search_code_index":

                try:

                    data = json.loads(
                        result
                    )


                    if isinstance(data, list) and data:

                        file = None

                        for item in data:

                            if "server-queue.cpp" in item.get(
                                "file",
                                ""
                            ):

                                file = item["file"]

                                break


                        if file is None:

                            file = data[0].get(
                                "file"
                            )

                        if file:

                            print(
                                "自动读取:",
                                file
                            )


                            chunk = self.controller.call(
                                "read_file_chunk",
                                {
                                    "path": file,
                                    "start": 1,
                                    "end": 200
                                }
                            )


                            observation = chunk

                            continue


                except Exception as e:

                    observation = str(result)

                    continue

            if tool == "read_file_chunk":

                parts = args.split("|")

                if len(parts) >= 3:

                    path = parts[0]

                    if (
                        path.endswith("llama.cpp")
                        and self.state["target_file"]
                    ):

                        parts[0] = self.state["target_file"]

                        args = "|".join(parts)

            if tool == "read_file_chunk":

                observation = str(result)

                continue



            observation = str(result) 
