def extract_toolimport json


from .planner import Planner
from .memory import Memory
from .validator import Validator
from .controller import Controller
from .code_analyzer import CodeAnalyzer



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


        self.memory = Memory()


        self.validator = Validator()


        self.controller = Controller(
            router,
            self.memory,
            self.validator
        )


        self.planner = Planner()


        self.analyzer = CodeAnalyzer(
            router
        )


        self.max_steps = 20



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


        return str(
            response
        )



    def extract_tool(
            self,
            text
        ):
    
    
        tools = [
    
            "search_code_index",
    
            "read_file",
    
            "read_file_chunk",
    
            "analyze_code"
    
        ]
    
    
        # 第一种:
        # <tool>args</tool>
    
        for tool in tools:
    
    
            start = (
                "<"
                + tool
                + ">"
            )
    
    
            end = (
                "</"
                + tool
                + ">"
            )
    
    
            if start in text and end in text:
    
    
                value = text.split(
                    start
                )[1].split(
                    end
                )[0].strip()
    
    
                return tool, value
    
    
    
        # 第二种:
        # # tool
    
        lines = text.splitlines()
    
    
        for index, line in enumerate(lines):
    
    
            for tool in tools:
    
    
                if tool in line:
    
    
                    if index + 1 < len(lines):
    
    
                        value = lines[
                            index + 1
                        ].strip()
    
    
                        if value:
    
                            return tool, value
    
    
    
        # 第三种:
        # 调用工具: xxx
    
    
        for line in lines:
    
    
            if "调用工具" in line:
    
    
                for tool in tools:
    
    
                    if tool in line:
    
    
                        return tool, ""
    
    
    
        return None, None
    
    

    def build_prompt(
        self,
        question,
        observation
    ):


        return f"""

你是一个代码分析 Agent。

项目:

{self.project}


用户任务:

{question}


最近工具结果:

{observation}


严格规则:

1. 不允许读取目录。

2. 如果工具返回真实文件路径，必须继续使用该路径。

3. 不允许猜测 /home/baixin/llama.cpp。

4. 分析源码必须经过:
search_code_index
-> read_file_chunk
-> 分析


5. 如果已经获得源码，不要重新搜索。


请继续执行下一步。


"""





        observation = ""

    def run(
        self,
        question
    ):

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


            print(
                response
            )



            tool, args = self.extract_tool(
                response
            )



            if not tool:


                print()

                print(
                    "分析完成"
                )

                break



            print()

            print(
                "执行工具:",
                tool
            )

            print(
                "参数:",
                args
            )



            result = self.controller.call(
                tool,
                args
            )



            print()

            print(
                "工具结果:"
            )

            print(
                result
            )



            observation = json.dumps(
                result,
                ensure_ascii=False
            )
