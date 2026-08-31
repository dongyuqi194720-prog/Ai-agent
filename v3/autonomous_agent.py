import copy
import json
import os
import re

from ai_agent.codex_bridge import CodexBridge


class AutonomousAgent:


    def __init__(
        self,
        llm,
        router,
        project=None
    ):

        self.llm = llm

        # V5.3:
        # 保留 V4 原有 self.llm。
        # Codex 作为独立第二推理通道接入，
        # 暂不改变 V4 状态机行为。
        self.codex = CodexBridge(
            model="gpt-5.6-terra"
        )

        self.router = router
        self.project = project
        self.max_steps = 20
        self.state = {
            "phase": "SEARCH",

            "search_done": False,

            "target_file": None,

            # V4.9.1: 保存 SEARCH 阶段发现的多个候选文件。
            # target_file 继续保留，兼容 V4.8.1 当前单文件流程。
            "target_files": [],

            # V4.9.2: 当前正在读取的候选文件索引。
            "read_index": 0,

            "read_done": False,

            "analysis_context": [], 

            "analyze_done": False,

            "analysis_result": "",

            "problem_confirmed": False,

            # V5.4:
            # Codex 独立分析结果。
            # 不覆盖 V4 原有 analysis_result。
            "codex_analysis": "",

            "codex_analyze_done": False,

            "verify_done": False,

            "summary_done": False,

            "can_modify": False,

            # V4.9.8:
            # VERIFY 确认存在明确问题后，
            # 先生成修改计划，再进入后续流程。
            "modify_plan_done": False,

            "modify_plan": "",

            # V4.9.9:
            # MODIFY_PLAN 生成后必须经过独立二次验证。
            # 未通过 PLAN_VERIFY 时禁止进入真正修改阶段。
            "plan_verify_done": False,

            "plan_verify_passed": False,

            "plan_verify_result": "",

            # V5.17:
            # MODIFY 完成后必须进入 VERIFY_RESULT，
            # 验证修改后的文件是否真实存在、语法是否正确。
            "verify_result_done": False,

            "verify_result_passed": False,

            "verify_result": "",

            "modified_file": None,

            "tool_failures": 0,

            # V4.9.7:
            # VERIFY 证据不足时允许重新 SEARCH。
            # 防止证据不足导致无限循环。
            "verify_retry_count": 0
        }
         

        # V6.1.7:
        # 保存一份干净的初始任务状态。
        self._initial_state = copy.deepcopy(
            self.state
        )

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



    def ask_codex(
        self,
        prompt
    ):

        # V5.3:
        # 独立 Codex 推理通道。
        # 不修改 V4 原有 ask_llm()。
        print(
            "V5.3 CODEX CALL START:",
            "input_chars =",
            len(str(prompt))
        )

        try:

            result = self.codex.ask(
                prompt
            )

        except Exception as e:

            # V5.8:
            # Codex 是独立第二意见。
            # Codex 失败时不得阻断 V4 主流程。
            print(
                "V5.8 CODEX CALL FAILED:",
                type(e).__name__,
                str(e)
            )

            return ""

        print(
            "V5.3 CODEX CALL END:",
            "output_chars =",
            len(str(result))
        )

        return result


    def ask_llm(
        self,
        prompt
    ):

        import time
        import threading
        import queue

        # V4.10.0:
        # LLM 调用不能无限阻塞 Agent。
        #
        # 注意：
        # 这里使用 daemon thread 执行 invoke。
        # 超时后主 Agent 可以继续恢复，而不会被
        # 一个永久阻塞的本地模型调用锁死。
        #
        # 当前先只实现“硬超时”。
        # 自动重试 / 状态恢复放到后续版本，
        # 避免一次修改同时改变状态机行为。

        LLM_TIMEOUT = 600

        start_time = time.time()

        print(
            "V4.10.0 LLM CALL START:",
            "input_chars =",
            len(str(prompt)),
            "timeout =",
            LLM_TIMEOUT,
            "seconds"
        )

        result_queue = queue.Queue(
            maxsize=1
        )

        def worker():

            try:

                response = self.llm.invoke(
                    prompt
                )

                result_queue.put(
                    (
                        "success",
                        response
                    )
                )

            except Exception as e:

                try:

                    result_queue.put(
                        (
                            "error",
                            e
                        )
                    )

                except Exception:
                    pass

        worker_thread = threading.Thread(
            target=worker,
            daemon=True
        )

        worker_thread.start()

        try:

            result_type, result = (
                result_queue.get(
                    timeout=LLM_TIMEOUT
                )
            )

        except queue.Empty:

            elapsed = time.time() - start_time

            print(
                "V4.10.0 LLM CALL TIMEOUT:",
                "elapsed =",
                round(elapsed, 2),
                "seconds"
            )

            print(
                "V4.10.0 LLM CALL TIMEOUT:",
                "Agent 不再无限等待模型"
            )

            raise TimeoutError(
                "LLM call timed out after "
                f"{LLM_TIMEOUT} seconds"
            )

        elapsed = time.time() - start_time

        if result_type == "error":

            print(
                "V4.10.0 LLM CALL ERROR:",
                type(result).__name__,
                "elapsed =",
                round(elapsed, 2),
                "seconds"
            )

            raise result

        print(
            "V4.10.0 LLM CALL END:",
            "elapsed =",
            round(elapsed, 2),
            "seconds"
        )

        if hasattr(
            result,
            "content"
        ):
            return result.content

        return str(result)



    def validate_summary(
        self,
        response
    ):
        """
        V4.4.2 SUMMARY Validator

        验证 SUMMARY 中“主要问题”的证据与结论是否一致。

        规则：
        1. “未发现明确问题”不能出现在一个被判定为明确问题的条目中。
        2. 仅描述代码行为，不能直接证明错误、冲突、崩溃、死锁等结论。
        3. 如果主要问题只有行为描述，没有明确的因果证据，
           则降级为“未发现明确问题”。
        """

        import re

        if not response:
            return response

        match = re.search(
            r"(2\.\s*主要问题.*?)(?=\n\s*3\.\s*修改方案)",
            response,
            re.S
        )

        if not match:
            return response

        problem_section = match.group(1)

        # --------------------------------------------------
        # V4.4.2 Rule 1:
        # “未发现明确问题”不能同时作为明确问题结论。
        # --------------------------------------------------

        if "未发现明确问题" in problem_section:
            print(
                "DEBUG: SUMMARY Validator 检测到无明确问题结论"
            )

            return self._replace_summary_problem(
                response,
                match
            )

        # --------------------------------------------------
        # V4.4.2 Rule 2:
        # 明确缺陷结论必须存在因果证据。
        #
        # 这里只检查结构，不针对具体项目或函数硬编码。
        # --------------------------------------------------

        strong_claim_patterns = [
            "会导致",
            "导致",
            "造成",
            "会发生",
            "可能导致",
            "可能造成",
            "可能发生",
            "可能存在",
            "潜在风险",
            "存在风险",
            "风险是",
            "会访问",
            "会崩溃",
            "会失败",
            "会出错",
            "会产生",
            "存在bug",
            "存在 bug",
            "发生冲突",
            "发生死锁",
            "发生数据竞争",
            "导致崩溃",
            "导致错误",
            "未定义行为",
            "悬空指针",
            "内存泄漏",
            "数据竞争",
            "死锁",
            "ID 冲突",
            "ID冲突",
            "必然",
        ]

        has_strong_claim = any(
            pattern in problem_section
            for pattern in strong_claim_patterns
        )

        # V5.19:
        # 明确区分“已证明的明确缺陷”和“未经证明的推测性风险”。
        # “可能导致 / 可能造成 / 可能发生 / 潜在风险”等表述
        # 即使同时出现“重复获取 / 同一线程”等机制词，
        # 也不能直接作为明确问题保留。
        uncertain_claim_patterns = [
            "可能导致",
            "可能造成",
            "可能发生",
            "可能存在",
            "潜在风险",
            "存在风险",
            "风险是",
            "可能会",
            "有可能",
        ]

        has_uncertain_claim = any(
            pattern in problem_section
            for pattern in uncertain_claim_patterns
        )

        if has_uncertain_claim:
            print(
                "DEBUG: SUMMARY Validator 检测到未经证明的推测性结论"
            )

            return self._replace_summary_problem(
                response,
                match
            )

        # --------------------------------------------------
        # V4.4.4:
        # 明确问题必须能够在“证据”中找到实际缺陷机制。
        #
        # 不能仅因为结论出现“会导致”就认为已经证明。
        # 例如：
        #
        #   证据：task.id == -1 时调用 id++
        #   结论：会导致 ID 冲突
        #
        # 证据只证明 ID 分配，没有证明冲突机制。
        #
        # 相反：
        #
        #   证据：已经释放对象后继续传递并解引用
        #   结论：会访问已释放对象并导致未定义行为
        #
        # 证据本身已经包含明确的错误机制。
        # --------------------------------------------------

        evidence_match = re.search(
            r"证据[：:]\s*(.*?)(?=\n\s*-\s*结论[：:]|\n\s*结论[：:]|\Z)",
            problem_section,
            re.S
        )

        conclusion_match = re.search(
            r"结论[：:]\s*(.*?)(?=\n\s*-\s*(?:说明|证据|结论)[：:]|\n\s*3\.\s*修改方案|\Z)",
            problem_section,
            re.S
        )

        evidence_text = (
            evidence_match.group(1).strip()
            if evidence_match
            else problem_section
        )

        conclusion_text = (
            conclusion_match.group(1).strip()
            if conclusion_match
            else ""
        )

        mechanism_patterns = [
            "已经释放",
            "已释放",
            "释放后",
            "释放之后",
            "继续使用",
            "继续传递",
            "随后解引用",
            "解引用",
            "访问已经释放",
            "访问已释放",
            "越界访问",
            "越界写入",
            "越界读取",
            "未初始化",
            "未初始化数据",
            "重复释放",
            "double free",
            "use-after-free",
            "空指针解引用",
            "悬空指针",
            "数据竞争",
            "死锁",
            "重复加锁",
            "重复获取",
            "同一线程",
            "锁顺序",
            "等待自身",
            "重复加锁",
            "重复获取同一把锁",
            "永久阻塞",
            "无限循环",
            "无限递归",
            "重复插入",
            "重复添加",
            "覆盖已有",
            "丢失数据",
            "错误返回",
        ]

        has_mechanism = any(
            pattern in evidence_text
            for pattern in mechanism_patterns
        )

        # --------------------------------------------------
        # V5.20:
        # 主要问题必须同时满足：
        #
        # 1. 存在明确缺陷结论；
        # 2. 证据中存在对应的错误机制。
        #
        # 如果只有“重复获取 mutex”“调用 notify_one”
        # “使用 unique_lock”等行为描述，
        # 但没有明确说明已经证明的错误结果，
        # 则不能作为“主要问题”保留。
        #
        # 同时保留：
        #
        #   证据：
        #   - 同一线程重复获取同一把非递归 mutex。
        #   结论：
        #   - 会发生死锁。
        #
        # 因为这里同时具备：
        #   strong_claim + mechanism
        # --------------------------------------------------

        if not has_strong_claim:
            print(
                "DEBUG: V5.20 SUMMARY Validator 检测到"
                "“只有行为描述，没有明确缺陷结论”"
            )

            return self._replace_summary_problem(
                response,
                match
            )

        # V6.1.1:
        # 普通业务逻辑错误不一定具有内存/并发类“错误机制”。
        #
        # 如果证据已经明确给出：
        #   1. 实际行为/结果
        #   2. 明确预期行为/结果
        #   3. 结论明确指出两者不一致构成错误
        #
        # 则可以直接保留，不要求命中
        # use-after-free / deadlock / race 等机制词。
        #
        # 这里只识别“明确结果不一致”，
        # 不放宽“可能导致”等推测性结论。

        business_error_patterns = [
            "实际结果",
            "预期结果",
            "实际返回",
            "应该返回",
            "实际值",
            "预期值",
            "实际输出",
            "预期输出",
            "结果与预期不符",
            "逻辑错误",
            "计算错误",
            "返回错误结果",
            "错误结果",
        ]

        has_business_error = (
            any(
                pattern in evidence_text
                for pattern in business_error_patterns
            )
            and
            any(
                pattern in conclusion_text
                for pattern in [
                    "错误",
                    "缺陷",
                    "不正确",
                    "不符合预期",
                    "与预期不符",
                ]
            )
        )

        if has_business_error:
            print(
                "DEBUG: V6.1.1 SUMMARY Validator 检测到"
                "明确业务结果错误，允许通过"
            )

            return response

        # 明确缺陷结论必须有对应的源码错误机制。
        if not has_mechanism:
            print(
                "DEBUG: V5.20 SUMMARY Validator 检测到"
                "“明确缺陷结论缺少证据机制”"
            )

            return self._replace_summary_problem(
                response,
                match
            )

        return response


    def _replace_summary_problem(
        self,
        response,
        match
    ):
        """
        将无法证明的主要问题统一降级。
        """

        import re

        replacement = """2. 主要问题
   - 未发现明确问题。

3. 修改方案
   - 当前未发现需要修改的明确问题。
"""

        after_problem = response[match.end():]

        # 原来的 response 已经包含“3. 修改方案”，
        # 因此只删除原问题区和原修改方案区，
        # 再重新插入统一结果。

        modification_match = re.search(
            r"\n\s*3\.\s*修改方案.*?(?=\n\s*4\.\s*测试建议)",
            after_problem,
            re.S
        )

        if modification_match:
            after_problem = (
                after_problem[:modification_match.start()]
                + after_problem[modification_match.end():]
            )

        return (
            response[:match.start()]
            + replacement
            + after_problem
        )


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


            # V4.6.2 Search Input Guard
            # 防止模型把项目路径当搜索关键词

            if args.startswith("/home/"):

                question = self.state.get(
                    "question",
                    ""
                ).lower()


                keywords = [
                    "mutex",
                    "thread",
                    "queue",
                    "server",
                    "scheduler",
                    "worker",
                    "lock",
                ]


                for k in keywords:

                    if k in question:

                        print(
                            "修正搜索关键词:",
                            args,
                            "->",
                            k
                        )

                        args = k
                        break


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


        # V6.1.2:
        # 兼容 Qwen 在 MODIFY 阶段输出的裸 write_file 格式。
        #
        # 实际可能输出：
        #
        # write_file
        # /absolute/path/file.py
        # 完整文件内容
        #
        # 只在文本去除前导空白后明确以 write_file 开头时解析，
        # 避免普通说明文字误触发。
        stripped_text = text.lstrip()

        if stripped_text.startswith("write_file"):
            lines = stripped_text.splitlines()

            if (
                len(lines) >= 3
                and lines[0].strip() == "write_file"
            ):
                path = lines[1].strip()

                file_content = "\n".join(
                    lines[2:]
                )

                if not path.startswith("/"):
                    print(
                        "V6.1.2 write_file 拒绝：路径不是绝对路径:",
                        path
                    )
                    return None, None

                if not file_content.strip():
                    print(
                        "V6.1.2 write_file 拒绝：文件内容为空"
                    )
                    return None, None

                print(
                    "V6.1.2 write_file 裸格式解析成功:",
                    path
                )

                return (
                    "write_file",
                    path + "|" + file_content
                )


        # V6.1:
        # MODIFY 阶段解析 write_file。
        #
        # 格式：
        # <write_file>
        # /absolute/path/file.py
        # 完整文件内容
        # </write_file>
        #
        # 第一行必须是文件路径。
        # 后续全部内容作为文件正文。
        m = re.search(
            r"<write_file>\s*(.*?)\s*</write_file>",
            text,
            re.S
        )

        if m:

            content = m.group(1).strip()

            lines = content.splitlines()

            if len(lines) < 2:
                return None, None

            path = lines[0].strip()

            file_content = "\n".join(
                lines[1:]
            )

            if not path.startswith("/"):
                print(
                    "V6.1 write_file 拒绝：路径不是绝对路径:",
                    path
                )
                return None, None

            if not file_content.strip():
                print(
                    "V6.1 write_file 拒绝：文件内容为空"
                )
                return None, None

            return (
                "write_file",
                path + "|" + file_content
            )


        return None,None

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

            "VERIFY": [],

            # V5.15:
            # PLAN_VERIFY 通过后进入 MODIFY。
            # MODIFY 第一阶段只允许执行 write_file。
            "MODIFY": [
                "write_file"
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
当前状态规则:

SEARCH阶段:
只能搜索代码。

READ阶段:
只能读取文件。

ANALYZE阶段:
只能分析代码，不允许修改。

SUMMARY阶段:
输出分析结论和修改建议。

没有完成完整分析前，禁止调用write_file。
当前 Agent 执行状态:

阶段:
{self.state["phase"]}

规则:

SEARCH阶段:
只能执行:
- search_code_index

READ阶段:
只能执行:
- read_file_chunk

ANALYZE阶段:
只能执行:
- analyze_code

VERIFY阶段:
验证分析结果是否有充分源码证据。
没有明确问题则进入SUMMARY。
发现明确问题则进入PLAN_VERIFY。

PLAN_VERIFY阶段:
验证修改计划是否有明确问题、明确文件、明确修改内容。
只有计划验证通过才能进入MODIFY。

MODIFY阶段:
只能执行:
- write_file

VERIFY_RESULT阶段:
验证修改是否成功。

SUMMARY阶段:
停止调用工具，输出总结。

严格限制:
1. 每次只能执行一个工具
2. 工具返回后重新判断
3. 不允许重复执行已经完成的阶段
4. 不允许跳过阶段
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
 ->
VERIFY
 ->
PLAN_VERIFY
 ->
MODIFY
 ->
VERIFY_RESULT
 ->
SUMMARY


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

        # V6.1.7:
        # 每次 run() 都从干净状态开始。
        # 防止上一个任务的状态污染新任务。
        self.state = copy.deepcopy(
            self._initial_state
        )

        self.state["question"] = question

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


            # V4.10.4:
            # READ 阶段的下一份文件已经由状态机确定。
            #
            # target_files[read_index] 是 SEARCH 阶段产生的候选文件列表，
            # 因此这里不再让 LLM 决定下一步是否读取文件。
            #
            # 这样可以避免：
            #
            # READ
            #   ↓
            # LLM
            #   ↓
            # Python 强制 read_file_chunk
            #
            # 对 3B 模型造成不必要的额外推理。
            #
            # LLM 只在真正需要分析源码时参与。

            if (
                self.state["phase"] == "READ"
                and self.state["target_files"]
                and self.state["read_index"]
                < len(self.state["target_files"])
            ):

                read_index = self.state["read_index"]

                target = self.state["target_files"][read_index]

                print()
                print(
                    "========== V4.10.4 DIRECT READ =========="
                )

                print(
                    "读取:",
                    read_index + 1,
                    "/",
                    len(self.state["target_files"])
                )

                print(
                    "文件:",
                    target
                )

                read_args = f"{target}|1|200"

                print(
                    "执行工具:",
                    "read_file_chunk"
                )

                print(
                    "参数:",
                    read_args
                )

                result = self.controller.call(
                    "read_file_chunk",
                    read_args
                )

                print(
                    "DEBUG tool returned:",
                    type(result)
                )

                print(
                    "DEBUG result length:",
                    len(str(result))
                )

                source = str(result)

                self.state["analysis_context"].append(
                    {
                        "file": target,
                        "source": source
                    }
                )

                print(
                    "V4.10.4 READ 完成:",
                    target
                )

                self.state["read_index"] += 1

                if (
                    self.state["read_index"]
                    < len(self.state["target_files"])
                ):

                    print(
                        "V4.10.4 继续直接读取下一个候选文件"
                    )

                    self.state["phase"] = "READ"

                else:

                    print(
                        "V4.10.4 所有候选文件读取完成"
                    )

                    self.state["read_done"] = True
                    self.state["phase"] = "ANALYZE"

                    print(
                        "V4.10.4 → ANALYZE"
                    )

                observation = (
                    "read_file_chunk 完成："
                    + target
                )

                continue


            if (
                self.state["phase"] == "VERIFY"
                and self.state["analyze_done"]
                and not self.state["verify_done"]
            ):

                print("========== VERIFY ==========")

                analysis = self.state.get(
                    "analysis_result",
                    ""
                )

                # V4.5.3:
                # VERIFY 不再只检查 analysis_result 是否存在，
                # 而是检查分析结果是否包含“证据 + 结论”结构。
                #
                # 明确问题必须同时具备：
                # 1. 证据描述
                # 2. 结论描述
                #
                # 只有“当前实现”或代码行为描述，
                # 不足以证明存在明确问题。


                analysis_text = str(analysis).strip()

                print(
                    "VERIFY: analysis type =",
                    type(analysis)
                )

                print(
                    "VERIFY: analysis length =",
                    len(analysis_text)
                )

                print(
                    "VERIFY: analysis preview =",
                    repr(analysis_text[:1000])
                )

                # V4.9.4:
                # VERIFY 不再只依赖关键词判断“是否存在证据”。
                #
                # 如果 ANALYZE 明确提出问题，
                # 必须提供结构化源码证据：
                #
                # 证据:
                # 文件: ...
                # 行号: ...
                # 代码: ...
                # 说明: ...
                #
                # 结论:
                # ...
                #
                # 如果无法提供完整证据，则 VERIFY 失败，
                # 回退 ANALYZE。

                # V5.9:
                # 只有最终“结论”明确写出“未发现明确问题”，
                # 才判定为无明确问题。
                # 不能因为证据/说明正文中出现这句话就误判。
                conclusion_probe = re.search(
                    r"结论\s*[:：]\s*(.*?)(?:\n|$)",
                    analysis_text,
                    re.S
                )

                conclusion_probe_text = (
                    conclusion_probe.group(1).strip()
                    if conclusion_probe
                    else ""
                )

                no_clear_problem = (
                    conclusion_probe_text == "未发现明确问题"
                )

                evidence_header = bool(
                    re.search(
                        r"证据\s*[:：]",
                        analysis_text
                    )
                )

                file_match = re.search(
                    r"文件\s*[:：]\s*(.+)",
                    analysis_text
                )

                line_match = re.search(
                    r"行号\s*[:：]\s*(.+)",
                    analysis_text
                )

                code_match = re.search(
                    r"代码\s*[:：]\s*(.+)",
                    analysis_text
                )

                explanation_match = re.search(
                    r"说明\s*[:：]\s*(.+)",
                    analysis_text
                )

                conclusion_match = re.search(
                    r"结论\s*[:：]\s*(.+)",
                    analysis_text
                )

                evidence_file = (
                    file_match.group(1).strip()
                    if file_match
                    else ""
                )

                evidence_line = (
                    line_match.group(1).strip()
                    if line_match
                    else ""
                )

                evidence_code = (
                    code_match.group(1).strip()
                    if code_match
                    else ""
                )

                evidence_explanation = (
                    explanation_match.group(1).strip()
                    if explanation_match
                    else ""
                )

                conclusion_text = (
                    conclusion_match.group(1).strip()
                    if conclusion_match
                    else ""
                )

                structured_evidence = (
                    evidence_header
                    and bool(evidence_file)
                    and bool(evidence_line)
                    and bool(evidence_code)
                    and bool(evidence_explanation)
                    and bool(conclusion_text)
                )

                # V6.1.4:
                # VERIFY 不仅检查证据格式，还检查证据是否真正
                # 来自本次 READ 的真实源码。
                #
                # 重要：
                # “证据代码存在于源码”只能证明引用真实，
                # 不能证明该代码存在业务错误。
                #
                # Python 业务逻辑错误如果没有明确的预期语义、
                # 调用约束或其他直接证据，不允许仅凭函数名称
                # 或 LLM 自己推测的“应该这样”认定为问题。

                evidence_source_match = False

                normalized_evidence_code = (
                    evidence_code
                    .replace("```python", "")
                    .replace("```", "")
                    .strip()
                )

                if evidence_file and normalized_evidence_code:

                    for item in self.state.get(
                        "analysis_context",
                        []
                    ):

                        real_file = str(
                            item.get("file", "")
                        ).strip()

                        real_source = str(
                            item.get("source", "")
                        )

                        if real_file != evidence_file:
                            continue

                        source_lines = real_source.splitlines()

                        try:
                            evidence_line_number = int(
                                re.search(
                                    r"\d+",
                                    evidence_line
                                ).group(0)
                            )
                        except Exception:
                            evidence_line_number = 0

                        if (
                            evidence_line_number >= 1
                            and evidence_line_number <= len(source_lines)
                        ):

                            start_index = max(
                                0,
                                evidence_line_number - 3
                            )

                            end_index = min(
                                len(source_lines),
                                evidence_line_number + 2
                            )

                            nearby_source = "\n".join(
                                line.strip()
                                for line in source_lines[
                                    start_index:end_index
                                ]
                            )

                            if normalized_evidence_code in nearby_source:
                                evidence_source_match = True

                        if evidence_source_match:
                            break

                print(
                    "VERIFY: evidence_source_match =",
                    evidence_source_match
                )

                if no_clear_problem:

                    verify_ok = (
                        self.state["analyze_done"]
                        and bool(analysis_text)
                    )

                else:

                    verify_ok = (
                        self.state["analyze_done"]
                        and bool(analysis_text)
                        and structured_evidence
                        and evidence_source_match
                    )

                print(
                    "VERIFY: structured_evidence =",
                    structured_evidence
                )

                print(
                    "VERIFY: evidence_file =",
                    bool(evidence_file)
                )

                print(
                    "VERIFY: evidence_line =",
                    bool(evidence_line)
                )

                print(
                    "VERIFY: evidence_code =",
                    bool(evidence_code)
                )

                print(
                    "VERIFY: evidence_explanation =",
                    bool(evidence_explanation)
                )

                print(
                    "VERIFY: conclusion =",
                    bool(conclusion_text)
                )

                print(
                    "VERIFY: no_clear_problem =",
                    no_clear_problem
                )

                if verify_ok:

                    print(
                        "VERIFY: 分析结果状态检查通过"
                    )

                    self.state["verify_done"] = True

                    # V5.14.2:
                    # 独立记录 VERIFY 是否确认存在明确问题。
                    # 不能使用 can_modify 判断，因为 PLAN_VERIFY
                    # 失败后 can_modify 会变成 False，但问题本身仍然存在。
                    self.state["problem_confirmed"] = (
                        not no_clear_problem
                    )

                    print(
                        "V5.14.2: problem_confirmed =",
                        self.state["problem_confirmed"]
                    )

                    # V4.8.0:
                    # VERIFY 已经确认分析结果是否包含明确问题。
                    # 这里将 VERIFY 结果转换为修改许可状态。
                    #
                    # 未发现明确问题：
                    #   不允许进入后续修改流程。
                    #
                    # 已确认明确问题：
                    #   允许后续版本生成修改计划。
                    self.state["can_modify"] = not no_clear_problem

                    print(
                        "MODIFY DECISION: can_modify =",
                        self.state["can_modify"]
                    )

                    if self.state["can_modify"]:

                        print(
                            "V4.9.8: 进入 MODIFY_PLAN"
                        )

                        self.state["modify_plan_done"] = False
                        self.state["modify_plan"] = ""

                        self.state["plan_verify_done"] = False
                        self.state["plan_verify_passed"] = False
                        self.state["plan_verify_result"] = ""

                        self.state["phase"] = "MODIFY_PLAN"

                    else:

                        self.state["phase"] = "SUMMARY"

                else:

                    print(
                        "VERIFY: 分析结果状态检查失败"
                    )

                    # V4.9.7:
                    # VERIFY 失败首先判断是否属于“证据不足”。
                    #
                    # 如果当前分析没有形成明确问题证据，
                    # 不再原地 ANALYZE。
                    #
                    # 重新进入 SEARCH，让 Agent 获取新的候选文件，
                    # 再经过 READ -> ANALYZE -> VERIFY。
                    #
                    # 最多重新调查 2 次，防止状态机无限循环。

                    self.state["verify_done"] = False

                    # VERIFY 未通过时绝对禁止修改。
                    self.state["can_modify"] = False

                    self.state["analyze_done"] = False

                    retry_count = self.state.get(
                        "verify_retry_count",
                        0
                    )

                    if retry_count < 2:

                        retry_count += 1

                        self.state["verify_retry_count"] = (
                            retry_count
                        )

                        print(
                            "V4.9.7: VERIFY 证据不足，重新 SEARCH"
                        )

                        print(
                            "V4.9.7: 调查轮次 =",
                            retry_count,
                            "/ 2"
                        )

                        self.state["search_done"] = False
                        self.state["target_file"] = None
                        self.state["target_files"] = []
                        self.state["read_index"] = 0
                        self.state["read_done"] = False
                        self.state["analysis_context"] = []

                        self.state["phase"] = "SEARCH"

                    else:

                        print(
                            "V4.9.7: 已达到最大重新调查次数"
                        )

                        print(
                            "V4.9.7: 停止继续 SEARCH，进入 SUMMARY"
                        )

                        self.state["phase"] = "SUMMARY"

                continue


            if (
                self.state["phase"] == "MODIFY_PLAN"
                and self.state["analyze_done"]
                and self.state["verify_done"]
                and self.state["can_modify"]
            ):

                print(
                    "========== MODIFY PLAN =========="
                )

                analysis = self.state.get(
                    "analysis_result",
                    ""
                )

                modify_prompt = f"""
根据下面已经通过 VERIFY 的代码分析结果，
生成一个结构化修改计划。

不要修改代码。
不要调用任何写文件工具。

只输出：

文件:
函数:
问题:
证据:
修改方案:
测试方案:
风险:

要求：
1. 必须严格基于已经提供的分析结果。
2. 不允许猜测不存在的代码。
3. 如果证据不足，明确说明无法生成可靠修改计划。
4. 修改方案必须说明具体准备修改什么。
5. 测试方案必须说明修改后如何验证。
6. 当前阶段只生成计划，不执行修改。
7. 修改方案必须保持原有程序的基本同步语义，不得为了消除一个问题而引入新的 mutex / lock / unlock 错误。
8. 对 mutex 问题必须逐一核对 lock 与 unlock 的实际执行顺序。
9. 如果同一线程连续执行：
   m.lock();
   m.lock();
   那么第二次 m.lock() 在第一次 unlock() 之前执行，属于同一非递归 std::mutex 的重复加锁路径。
10. 针对同一线程重复加锁，禁止把两个 lock() 简单替换成两个同名的 std::unique_lock 或 std::lock_guard 声明，因为这会产生重复变量定义或错误的锁管理。
11. 如果使用 RAII，必须明确只创建一个合法的锁对象，并删除多余的重复加锁操作；不得保留会再次获取同一把非递归 mutex 的 lock()。
12. 修改方案中的示例代码必须是合法、可编译的 C++，并且不能假设分析结果中不存在的函数、变量或线程。

已经验证的分析结果：
{analysis}
"""

                plan = self.ask_llm(
                    modify_prompt
                )

                self.state["modify_plan"] = str(
                    plan
                )

                self.state["modify_plan_done"] = True

                print(
                    "V4.9.8 MODIFY PLAN 完成"
                )

                print(
                    self.state["modify_plan"]
                )

                print(
                    "V4.9.9: MODIFY_PLAN 完成，进入 PLAN_VERIFY"
                )

                self.state["phase"] = "PLAN_VERIFY"

                continue


            if (
                self.state["phase"] == "PLAN_VERIFY"
                and self.state["analyze_done"]
                and self.state["verify_done"]
                and self.state["can_modify"]
                and self.state["modify_plan_done"]
            ):

                print(
                    "========== PLAN VERIFY =========="
                )

                analysis = str(
                    self.state.get(
                        "analysis_result",
                        ""
                    )
                )

                plan = str(
                    self.state.get(
                        "modify_plan",
                        ""
                    )
                )

                # V6.1:
                # 根据目标文件类型选择 PLAN_VERIFY 规则。
                #
                # Python 不使用 C/C++ mutex/lock/unlock 审查规则。
                # C/C++ 保留原有并发安全 PLAN_VERIFY。

                plan_verify_file = ""

                if self.state.get("target_files"):
                    plan_verify_file = str(
                        self.state["target_files"][0]
                    )

                elif self.state.get("target_file"):
                    plan_verify_file = str(
                        self.state["target_file"]
                    )

                if plan_verify_file.lower().endswith(".py"):

                    plan_verify_prompt = f"""
你现在是独立的 Python 代码修改方案审查者。

不要修改代码。
不要调用任何工具。
不要假设没有提供的源码。

===== 已验证的问题分析 =====
{analysis}

===== 修改计划 =====
{plan}

请严格判断：

1. 问题是否真的由提供的 Python 源码证据证明。
2. 修改计划是否真正针对已经确认的问题。
3. 修改是否与实际源码中的函数、参数和控制流程一致。
4. 修改后的行为是否能够直接解决已经确认的问题。
5. 修改是否会破坏现有调用关系。
6. 修改方案是否明确指出修改文件和修改位置。
7. 测试方案是否能够验证修改后的实际行为。
8. 如果问题分析不成立，必须 FAIL。
9. 如果修改方案与问题无关，必须 FAIL。
10. 如果源码证据不足以支持修改，必须 FAIL。

重要规则：

- 本次是 Python 文件。
- 不要求检查 mutex、lock、unlock、condition_variable、thread 或 atomic。
- 不得因为 Python 文件没有 mutex、lock 或 unlock 而判定 FAIL。
- 不得套用 C/C++ 并发修改规则。
- 只能根据已经提供的源码证据和修改计划判断。
- 不得猜测没有提供的代码。
- “可能”“推测”“看起来”不能作为 PASS 的依据。

只输出：

PLAN_VERIFY:
PASS 或 FAIL

理由:
<简短说明>

如果 FAIL：
必须明确指出：
1. 问题分析哪里不成立；
2. 修改方案哪里存在风险。

如果 PASS：
必须说明为什么现有源码证据足以支持该修改。

===== 当前目标文件 =====
{plan_verify_file}
"""

                else:

                    plan_verify_prompt = f"""
你现在是独立的代码修改方案审查者。

不要修改代码。
不要调用任何工具。
不要假设没有提供的源码。

请独立判断下面的“问题分析”和“修改计划”是否成立。

===== 已验证的问题分析 =====
{analysis}

===== 修改计划 =====
{plan}

重点检查：

1. 问题是否真的由提供的代码证据证明。
2. 修改计划是否真正解决该问题。
3. 修改是否可能破坏 mutex / lock / condition_variable / thread / atomic 的正确同步关系。
4. 修改是否可能把原本受锁保护的数据访问变成无锁访问。
5. 修改是否可能引入新的数据竞争。
6. 修改是否可能破坏条件变量通知与等待关系。
7. 修改是否可能改变原有执行顺序并产生新的并发问题。
8. 如果当前证据不足，必须拒绝修改。
9. 如果问题本身不成立，也必须拒绝修改。

修改方案完整性检查：

1. 修改方案必须同时检查被修改代码与对应的 unlock()。
2. 如果原代码存在多个 lock() 和 unlock()，
   不能只替换其中的 lock()，却不说明原有 unlock() 如何处理。
3. 如果修改方案使用 std::unique_lock 或 std::lock_guard，
   必须明确说明原有手工 lock() 和 unlock() 哪些删除、
   哪些保留，以及新的锁对象作用域。
4. 如果修改后可能同时存在 RAII 自动解锁和原有手工 unlock()，
   必须判定为 FAIL。
5. 如果修改方案没有提供足够证据证明修改后的锁获取、
   锁释放和作用域关系正确，必须判定为 FAIL。
6. 不能仅因为 unique_lock 或 lock_guard 能自动管理 mutex，
   就认定修改方案正确。
7. 对明确的同线程重复加锁问题，
   PASS 的必要条件是修改方案明确删除重复获取同一 mutex 的操作，
   并且完整说明对应 unlock() 的处理方式。

同一线程重复加锁的明确判定规则：

如果提供的代码证据明确显示同一执行路径连续执行：
m.lock();
m.lock();
并且第一次 lock() 与第二次 lock() 之间没有对应的 unlock()，
同时 m 的类型明确为非递归 std::mutex，
则已经足以证明存在同线程重复加锁的死锁路径。

此时：
1. 不需要额外证明存在其他线程。
2. 不得以“没有其他线程信息”为理由否定该问题。
3. 后面的 unlock() 不能证明安全，因为第二次 lock() 成功返回之前，后续代码无法继续执行。
4. “lock 次数和 unlock 次数相等”不能否定该死锁，因为执行顺序必须先通过第二次 lock() 才能到达后面的 unlock()。
5. 对这种明确的同线程自死锁问题，PLAN_VERIFY 应重点判断修改方案是否真正删除了重复获取同一 mutex 的操作。

特别注意：

“代码在 mutex 内执行”本身不能证明存在数据竞争。
如果 mutex 正是在保护该共享数据，那么持锁访问通常是正确行为。
不能仅凭“持锁期间调用 push_back / push_front / erase / update”
就认定存在并发问题。

只输出：

PLAN_VERIFY:
PASS 或 FAIL

理由:
<简短说明>

如果 FAIL：
必须明确指出：
1. 问题分析哪里不成立；
2. 修改方案哪里存在风险。

如果 PASS：
必须说明为什么现有证据足以支持该修改。

禁止使用“可能”“看起来”“推测”等模糊理由作为 PASS 的依据。
"""

                plan_verify_result = self.ask_llm(
                    plan_verify_prompt
                )

                plan_verify_result = str(
                    plan_verify_result
                )

                self.state["plan_verify_result"] = (
                    plan_verify_result
                )

                upper_result = (
                    plan_verify_result.upper()
                )

                plan_passed = (
                    "PLAN_VERIFY:" in upper_result
                    and "PASS" in upper_result
                    and "FAIL" not in upper_result
                )

                self.state["plan_verify_done"] = True
                self.state["plan_verify_passed"] = (
                    plan_passed
                )

                print(
                    "PLAN_VERIFY result =",
                    plan_verify_result
                )

                print(
                    "PLAN_VERIFY passed =",
                    plan_passed
                )

                if plan_passed:

                    print(
                        "V4.9.9: 修改方案二次验证通过"
                    )

                    # V5.15:
                    # PLAN_VERIFY 通过后进入真正修改阶段。
                    # 当前先建立 MODIFY 状态入口。
                    self.state["phase"] = "MODIFY"

                else:

                    print(
                        "V4.9.9: 修改方案二次验证失败"
                    )

                    print(
                        "V4.9.9: 禁止进入修改阶段"
                    )

                    self.state["can_modify"] = False

                    self.state["phase"] = "SUMMARY"

                continue


            if (
                self.state["phase"] == "SUMMARY"
                and self.state["analyze_done"]
                and self.state["verify_done"]
            ):

                print("进入总结阶段")

                analysis = self.state.get(
                    "analysis_result",
                    ""
                )

                # V5.14.1:
                # SUMMARY 必须区分：
                # 1. 没有发现明确问题
                # 2. 已发现明确问题，但 PLAN_VERIFY 未通过
                #
                # 这里只向 SUMMARY 提供状态事实，
                # 不改变 VERIFY / MODIFY / PLAN_VERIFY 判定。
                can_modify = self.state.get(
                    "can_modify",
                    False
                )

                problem_confirmed = self.state.get(
                    "problem_confirmed",
                    False
                )

                plan_verify_passed = self.state.get(
                    "plan_verify_passed",
                    False
                )

                verify_result_done = self.state.get(
                    "verify_result_done",
                    False
                )

                verify_result_passed = self.state.get(
                    "verify_result_passed",
                    False
                )

                modification_status = f"""
本轮修改状态：
- 已发现明确问题：{"是" if problem_confirmed else "否"}
- PLAN_VERIFY：{"通过" if plan_verify_passed else "未通过"}
- 当前允许修改：{"是" if can_modify else "否"}
- VERIFY_RESULT：{"通过" if verify_result_passed else ("未通过" if verify_result_done else "未执行")}

重要规则：
“当前允许修改 = 否”不等于“未发现明确问题”。

如果“主要问题”已经被源码证明存在，
但 PLAN_VERIFY 未通过，导致当前禁止修改，
“修改方案”必须明确说明：
“修改计划验证未通过，本轮未执行修改。”
禁止写成“无需修改”或“未发现需要修改的问题”。
"""

                print(
                    "V5.14.2: SUMMARY修改状态：",
                    "problem_confirmed =",
                    problem_confirmed,
                    "can_modify =",
                    can_modify,
                    "plan_verify_passed =",
                    plan_verify_passed
                )

                # V4.4 chunked analysis
                # 不把完整源码一次性送入 SUMMARY。
                # 将分析结果分成最多 3 个源码块，
                # 分别让 LLM 提取关键实现信息。

                chunk_size = 3000
                chunks = [
                    analysis[i:i + chunk_size]
                    for i in range(
                        0,
                        len(analysis),
                        chunk_size
                    )
                ]

                chunks = chunks[:3]

                self.state["analysis_context"] = []

                print(
                    "DEBUG: ANALYZE 分块数量 =",
                    len(chunks)
                )

                for i, chunk in enumerate(chunks):

                    print(
                        "DEBUG: 分析源码块",
                        i + 1,
                        "/",
                        len(chunks),
                        "长度 =",
                        len(chunk)
                    )

                    chunk_prompt = f"""
分析下面的代码分析结果片段。

这是第 {i + 1} / {len(chunks)} 个片段。

代码分析结果片段：
{chunk}

请只提取与代码理解有关的事实：

1. 主要实现和职责
2. 关键函数或数据结构
3. 并发、队列、状态或资源管理机制
4. 明显的风险或值得注意的地方

不要猜测没有出现的代码。
不要提出泛泛的修改建议。
不要重复大段源码。
使用简短中文回答。
"""

                    chunk_analysis = self.ask_llm(
                        chunk_prompt
                    )

                    self.state["analysis_context"].append(
                        chunk_analysis
                    )

                    print(
                        "DEBUG: 源码块",
                        i + 1,
                        "分析完成，结果长度 =",
                        len(str(chunk_analysis))
                    )

                combined_analysis = "\n\n".join(
                    self.state["analysis_context"]
                )

                print(
                    "DEBUG: 分块分析结果总长度 =",
                    len(combined_analysis)
                )

                summary_prompt = f"""
根据下面的原始代码分析结果和分块分析结果，写一个非常简短的最终报告。

【原始代码分析结果】
{analysis[:12000]}

【分块分析结果】
{combined_analysis}

【本轮修改状态】
{modification_status}

重要规则：
1. 原始代码分析结果是最高优先级证据。
3. 分块分析结果只是辅助理解，不能覆盖原始代码中的事实。
4. 如果分块分析与原始代码分析结果冲突，以原始代码分析结果为准。
5. 不得根据常识补充源码中没有出现的行为。
6. 不得把“可能存在”写成“已经存在”。
7. 如果无法从代码中确认问题，必须写“未发现明确问题”。

只回答以下四项：

1. 当前实现
2. 主要问题
3. 修改方案
4. 测试建议

每项最多 2 句话。

必须基于分块分析中明确出现的信息。

问题判断规则：

1. “主要问题”只能填写已经被源码直接证明的问题，不能填写普通设计选择、潜在风险、改进建议或“需要确认”的事项。

2. 每一个“主要问题”必须同时满足以下三个条件：
   - 能指出具体函数、变量、数据结构或代码语句；
   - 能说明该代码实际执行了什么行为；
   - 能明确说明为什么这个行为构成错误、缺陷或违反预期。
   如果缺少其中任何一项，就不能列为“主要问题”。

3. 每个主要问题必须按照下面格式输出：

   - 证据：指出具体函数、变量或代码行为。
   - 结论：说明该行为已经造成的明确错误或缺陷。

   “证据”只是证明代码做了什么，不能直接等于“问题”。
   必须同时证明为什么该行为是错误的。

   例如：
   “task.id == -1 时调用 id++”
   只能证明代码会分配 ID，
   不能据此推出“会发生 ID 冲突”。

   如果源码没有证明冲突实际可能发生，就必须写：
   “未发现明确问题”。


3. 以下情况默认不能判定为问题：
   - 调用了另一个函数，但没有证据证明该调用错误；
   - 使用 mutex、condition_variable、线程、队列或状态变量，但没有具体的数据竞争、死锁、逻辑错误证据；
   - 参数可能为特殊值，但代码已经对该值进行了明确处理；
   - 某个行为“可能导致”问题，但没有源码证据证明实际会发生；
   - 仅仅认为代码可以“更安全”“更完善”或“应该增加检查”。

4. 特别注意：
   - 不要把“代码存在某个分支”写成“该分支存在 bug”。
   - 不要把“调用 cleanup_pending_task”直接判定为错误，除非能从源码证明该调用会产生错误结果。
   - 不要把 task.id == -1 自动判定为问题；必须区分不同 post 重载中的实际处理逻辑。
   - 如果代码通过 GGML_ASSERT、条件判断、锁或其他机制已经处理了某种情况，不要重复把该情况报告成未处理问题。

5. 如果只能发现潜在风险，写成“潜在风险”，不要写成“主要问题”。

5.1 “当前实现”同样必须严格区分代码事实和推测：
   - 只能描述源码中已经确认发生的行为。
   - 禁止把“可能导致”“可能造成”“可能发生”“存在风险”等推测性结论，
     当作当前实现的确定事实。
   - 例如：
     “task.id == -1 时调用 id++”
     可以作为代码事实。
   - 但：
     “task.id == -1 时可能导致 ID 冲突”
     只有在源码中已经证明存在重复 ID 的具体机制时才能写。
   - 如果没有证明冲突机制，只能描述实际代码行为，
     不得自行扩展成风险判断。
   - 测试建议可以验证这种行为，但测试建议不能反向证明代码存在问题。

6. 如果没有足够证据确认问题，必须明确写：
   “未发现明确问题。”
   不得为了填充报告而强行提出问题。

7. “修改方案”必须严格服从“主要问题”，两者一一对应。

   如果“主要问题”中写的是：
   “未发现明确问题。”
   那么“修改方案”必须原样写：
   “当前未发现需要修改的明确问题。”

   禁止在这种情况下提出任何新增检查、增加锁、增加异常处理、
   增加 ID 校验、修改函数逻辑或其他改进建议。

   只有当“主要问题”已经证明存在明确错误或缺陷时，
   才允许提出对应的修改方案。

   修改方案不得使用“建议检查”“可以增加”“应该添加”“建议完善”
   等没有明确问题依据的改进性表述。

8. 测试建议可以针对关键行为，但不得把测试建议反过来当成代码存在问题的证据。

9. 最终报告中的每个问题，都必须能够在原始代码分析结果中找到对应证据。

10. “当前实现”只能描述源码中已经确认发生的代码行为。
    禁止在“当前实现”中加入未经证明的因果判断。

    例如：
    “post 方法在 task.id == -1 时调用 id++”
    是合法的事实描述。

    但：
    “post 方法在 task.id == -1 时调用 id++，可能导致 ID 冲突”
    不合法，因为源码没有证明 ID 冲突实际发生。

    如果无法证明错误，只描述代码行为本身。

11. “测试建议”只能描述需要验证的行为，
    不得通过测试目标暗示代码已经存在问题。

    错误：
    “测试 ID 分配，确保不会分配新的 ID。”

    正确：
    “测试 task.id == -1 时的 ID 分配行为。”

12. 四个报告部分必须保持严格的语义边界：

    当前实现 = 已确认的代码事实。
    主要问题 = 已经被源码证明的明确缺陷。
    修改方案 = 只针对已经证明的明确缺陷。
    测试建议 = 对关键行为进行中性验证。

    不允许通过“当前实现”或“测试建议”绕过“主要问题”的证据要求。

13. 如果“主要问题”最终为“未发现明确问题”，
    “当前实现”仍然可以正常描述代码行为，
    但不得把这些行为描述成风险、缺陷或可能导致的问题。

14. “测试建议”必须保持中性。
    如果“主要问题”为“未发现明确问题”，
    测试建议只能描述需要验证的实际代码行为，
    不得加入未经证明的缺陷假设。

    例如：

    错误：
    “测试 task.id == -1 时的 ID 分配行为，确保不会发生 ID 冲突。”

    正确：
    “测试 task.id == -1 时的 ID 分配行为。”

    禁止使用以下方式暗示未证明的问题：
    - 确保不会发生……
    - 确保不会导致……
    - 验证是否存在……
    - 防止……
    - 避免……
    - 确保不会冲突……

    除非“主要问题”已经由源码明确证明对应缺陷，
    否则测试建议不得把某种结果预设为错误。

不要调用工具。
不要重复源码。
不要输出 git 提交步骤。
"""

                print("DEBUG: 当前阶段 =", self.state["phase"])
                print("DEBUG: 开始生成 SUMMARY")

                response = self.ask_llm(
                    summary_prompt
                )

                print("DEBUG: SUMMARY 生成完成")

                response = self.validate_summary(
                    response
                )

                print()
                print("========== 最终报告 ==========")
                print(response)

                self.state["summary_done"] = True

                print()
                print("========== 任务完成 ==========")

                break


            # V5.16:
            # PLAN_VERIFY 通过后进入 MODIFY。
            # 只允许根据已经验证的修改计划生成 write_file。
            if (
                self.state["phase"] == "MODIFY"
                and self.state["modify_plan_done"]
                and self.state["plan_verify_done"]
                and self.state["plan_verify_passed"]
                and self.state["can_modify"]
            ):

                print()
                print("========== MODIFY ==========")

                modify_plan = str(
                    self.state.get(
                        "modify_plan",
                        ""
                    )
                )

                modify_prompt = f"""
你现在执行已经通过 PLAN_VERIFY 的代码修改计划。

严格要求：
1. 只根据下面的修改计划执行。
2. 不得修改计划之外的文件。
3. 不得猜测不存在的源码。
4. 只输出一个 write_file 工具调用。
5. write_file 的 path 必须是修改计划明确指定的文件。
6. content 必须是修改后的完整文件内容。
7. 不允许输出解释。
8. 不允许调用其他工具。

===== 已验证修改计划 =====
{modify_plan}

输出格式：

<write_file>
path
完整修改后的文件内容
</write_file>
"""

                response = self.ask_llm(
                    modify_prompt
                )

                print(response)

                tool, args = self.extract_tool(
                    response
                )

                if tool != "write_file":

                    print(
                        "V5.16: MODIFY 未生成 write_file，禁止修改"
                    )

                    self.state["can_modify"] = False
                    self.state["phase"] = "SUMMARY"

                    continue

                if not self.allow_tool(tool):

                    print(
                        "V5.16: MODIFY 当前禁止执行:",
                        tool
                    )

                    self.state["can_modify"] = False
                    self.state["phase"] = "SUMMARY"

                    continue

                print(
                    "V5.16: 执行 write_file"
                )

                result = self.controller.call(
                    tool,
                    args
                )

                print(
                    "DEBUG MODIFY result:",
                    result
                )

                # V5.17:
                # MODIFY 成功后必须进入 VERIFY_RESULT，
                # 不能直接进入 SUMMARY。
                self.state["modified_file"] = args.split("|", 1)[0].strip()
                self.state["verify_result_done"] = False
                self.state["verify_result_passed"] = False
                self.state["verify_result"] = ""
                self.state["phase"] = "VERIFY_RESULT"

                print(
                    "V5.17: MODIFY 完成，进入 VERIFY_RESULT"
                )

                continue


            # V5.17:
            # MODIFY 后进入 VERIFY_RESULT。
            # 读取实际修改后的文件，验证修改是否真实落地。
            if (
                self.state["phase"] == "VERIFY_RESULT"
                and self.state["modified_file"]
            ):

                print()
                print("========== VERIFY RESULT ==========")

                modified_file = self.state["modified_file"]

                verify_result = self.controller.call(
                    "read_file_chunk",
                    f"{modified_file}|1|300"
                )

                verify_prompt = f"""
验证刚刚执行的代码修改是否真实成功。

修改文件:
{modified_file}

修改后的实际源码:
{verify_result}

只判断：
1. 文件是否存在并成功读取。
2. 修改是否已经实际出现在源码中。
3. 修改后的代码是否明显存在语法错误或结构错误。

不要修改代码。
不要调用工具。

只输出：

VERIFY_RESULT:
PASS 或 FAIL

理由:
<简短说明>
"""

                result = self.ask_llm(
                    verify_prompt
                )

                result = str(result)

                self.state["verify_result"] = result
                self.state["verify_result_done"] = True

                upper_result = result.upper()

                passed = (
                    "VERIFY_RESULT:" in upper_result
                    and "PASS" in upper_result
                    and "FAIL" not in upper_result
                )

                self.state["verify_result_passed"] = passed

                print(
                    "VERIFY_RESULT result =",
                    result
                )

                print(
                    "VERIFY_RESULT passed =",
                    passed
                )

                self.state["phase"] = "SUMMARY"

                continue


            response = self.ask_llm(
                prompt
            )


            print(response)


            tool, args = self.extract_tool(
                response
            )

            # V6.1.5:
            # SEARCH 阶段由状态机强制执行 search_code_index。
            # VERIFY FAIL -> SEARCH 时，不允许 LLM 再决定读取文件。

            if self.state["phase"] == "SEARCH":

                search_question = str(
                    self.state.get(
                        "question",
                        question
                    )
                )

                # V6.1.6:
                # 用户明确提供真实源码文件时，
                # SEARCH 阶段直接进入 READ。
                #
                # 避免将完整自然语言任务错误地交给
                # search_code_index 后触发无效 JSON 解析。

                path_match = re.search(
                    r"(/[^\\s]+\\.(?:py|cpp|cc|c|h|hpp))",
                    search_question
                )

                if (
                    path_match
                    and os.path.isfile(path_match.group(1))
                ):

                    candidate = path_match.group(1)

                    self.state["target_files"] = [candidate]
                    self.state["target_file"] = candidate
                    self.state["read_index"] = 0
                    self.state["analysis_context"] = []
                    self.state["read_done"] = False

                    print(
                        "V6.1.6 exact file bypass:",
                        candidate
                    )

                    self.state["phase"] = "READ"

                    continue

                tool = "search_code_index"

                args = search_question

                print(
                    "V6.1.5 SEARCH 强制执行:",
                    tool
                )

                print(
                    "V6.1.5 SEARCH 参数:",
                    args
                )

            # V4.3.2 force read after search

            if (
                self.state["target_file"]
                and tool == "search_code_index"
            ):

                print(
                      "已有目标文件，禁止再次搜索"
                )

                tool = "read_file_chunk"

                args = (
                        self.state["target_file"]
                        + "|1|100"
                )

            # V4.3.4 force analyze phase

            if (
                self.state["phase"] == "ANALYZE"
                and tool != "analyze_code"
            ):

                print(
                      "分析阶段强制执行 analyze_code"
                )

                tool = "analyze_code"

                args = (
                self.state["target_file"]
                )

            # V4.9.2 READ 阶段：
            # 按 SEARCH 阶段产生的 Top 5 候选文件依次读取。
            #
            # LLM 不允许决定 READ 路径。
            # 当前文件由 read_index + target_files 决定。
            if (
                self.state["phase"] == "READ"
                and self.state["target_files"]
            ):

                read_index = self.state["read_index"]

                if read_index < len(self.state["target_files"]):

                    target = self.state["target_files"][read_index]

                    print(
                        "V4.9.2 READ candidate:",
                        read_index + 1,
                        "/",
                        len(self.state["target_files"]),
                        target
                    )

                    tool = "read_file_chunk"

                    args = f"{target}|1|200"

                else:

                    print(
                        "V4.9.2 所有候选文件读取完成"
                    )

                    self.state["read_done"] = True
                    self.state["phase"] = "ANALYZE"

                    continue

            if (
                self.state["phase"] == "READ"
                and tool == "search_code_index"
            ):
                tool = "read_file_chunk"


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

                if self.state["phase"] == "SUMMARY":
                    break

                continue


            print(
                "执行工具:",
                tool
            )


            print(
                "参数:",
                args
            )

            # V5.18:
            # 禁止在 controller.call() 之前读取项目目录。
            # 旧逻辑在 call() 之后才检查，目录已经被实际读取，
            # 并且错误 result 还可能进入 analysis_context。
            if tool == "read_file_chunk":

                read_path = args.split("|", 1)[0].strip()

                if read_path == self.project:

                    print(
                        "V5.18: 禁止读取项目目录，改为读取已确认目标文件"
                    )

                    if self.state["target_file"]:

                        args = (
                            self.state["target_file"]
                            + "|1|100"
                        )

                    else:

                        print(
                            "V5.18: 没有有效 target_file，拒绝本次读取"
                        )

                        continue

            # V4.9.2：
            # READ 阶段允许连续读取多个候选文件。
            # 不再使用旧版 read_done 阻止后续 READ。

            result = self.controller.call(
                tool,
                args
            )

            print("DEBUG tool returned:", type(result))
            print("DEBUG result length:", len(str(result)))

            if tool == "read_file_chunk":

                # V4.9.2：
                # 保存当前候选文件源码，供后续多文件分析使用。
                self.state["analysis_context"].append(
                    {
                        "file": self.state["target_files"][
                            self.state["read_index"]
                        ],
                        "source": str(result)
                    }
                )

                print(
                    "V4.9.2 READ 完成:",
                    self.state["target_files"][
                        self.state["read_index"]
                    ]
                )

                self.state["read_index"] += 1

                if (
                    self.state["read_index"]
                    < len(self.state["target_files"])
                ):

                    print(
                        "V4.9.2 继续读取下一个候选文件"
                    )

                    self.state["phase"] = "READ"

                else:

                    print(
                        "V4.9.2 所有候选文件读取完成，进入分析阶段"
                    )

                    self.state["read_done"] = True
                    self.state["phase"] = "ANALYZE"



            if tool == "analyze_code":

                print()
                print("========== ANALYZE 完成 ==========")

                print("DEBUG: 开始 LLM 分析")

                # V4.9.3 step2:
                # 多文件分析上下文最多包含 5 个候选文件，
                # 每个文件最多 1000 字符。
                #
                # 总长度上限提高到 6000，
                # 避免 V4.7.1 的 4000 字符限制截断多文件上下文。
                MAX_ANALYSIS_CHARS = 10000

                # V4.9.2 step3:
                # ANALYZE 不再只分析最后一次 READ 的 result。
                # 将 SEARCH 阶段选出的多个候选文件统一送入分析上下文。
                #
                # 每个文件最多保留 1000 字符，
                # 防止多个大型源码文件导致本地模型推理时间过长。

                # V4.10.1:
                # 先提取并发相关源码证据，再限制总上下文长度。
                #
                # 不再使用 source[:2000]，
                # 避免 mutex / lock / wait / notify 位于文件后部
                # 时被直接截断。

                CONCURRENCY_KEYWORDS = (
                    "std::mutex",
                    "std::recursive_mutex",
                    "std::unique_lock",
                    "std::lock_guard",
                    "std::scoped_lock",
                    "std::condition_variable",
                    "std::condition_variable_any",
                    "wait(",
                    "wait_for(",
                    "wait_until(",
                    "notify_one(",
                    "notify_all(",
                    "std::thread",
                    "std::jthread",
                    "std::atomic",
                    "atomic_flag",
                    ".lock(",
                    ".unlock(",
                    " lock();",
                    " unlock();",
                )

                MAX_ANALYSIS_CHARS = 1800
                CONTEXT_LINES = 3
                MAX_FILE_EVIDENCE_CHARS = 1800

                analysis_parts = []

                for item in self.state["analysis_context"]:

                    file_path = item.get("file", "")
                    source = str(item.get("source", ""))

                    if not source.strip():
                        continue

                    lines = source.splitlines()

                    matched_indexes = []

                    for index, line in enumerate(lines):

                        if any(
                            keyword in line
                            for keyword in CONCURRENCY_KEYWORDS
                        ):
                            matched_indexes.append(index)

                    if matched_indexes:

                        evidence_indexes = set()

                        for index in matched_indexes:

                            begin = max(
                                0,
                                index - CONTEXT_LINES
                            )

                            end_index = min(
                                len(lines),
                                index + CONTEXT_LINES + 1
                            )

                            for line_index in range(
                                begin,
                                end_index
                            ):
                                evidence_indexes.add(
                                    line_index
                                )

                        ordered_indexes = sorted(
                            evidence_indexes
                        )

                        evidence_lines = []

                        for line_index in ordered_indexes:

                            evidence_lines.append(
                                f"{line_index + 1}: "
                                + lines[line_index]
                            )

                        evidence = "\n".join(
                            evidence_lines
                        )

                        if len(evidence) > MAX_FILE_EVIDENCE_CHARS:

                            evidence = (
                                evidence[
                                    :MAX_FILE_EVIDENCE_CHARS
                                ]
                                + "\n[V4.10.1: 该文件并发证据已截断]"
                            )

                        analysis_parts.append(
                            f"===== 并发证据文件: {file_path} =====\n"
                            + evidence
                        )

                    else:

                        # 没有并发关键词的文件只保留少量摘要。
                        summary = "\n".join(
                            lines[:12]
                        )

                        if summary.strip():

                            analysis_parts.append(
                                f"===== 非并发核心文件摘要: {file_path} =====\n"
                                + summary
                            )

                analysis_source = "\n\n".join(
                    analysis_parts
                )

                print(
                    "V4.10.1 ANALYZE 文件数量 =",
                    len(analysis_parts)
                )

                print(
                    "V4.10.1 并发证据初始长度 =",
                    len(analysis_source)
                )

                if len(analysis_source) > MAX_ANALYSIS_CHARS:

                    analysis_source = (
                        analysis_source[:MAX_ANALYSIS_CHARS]
                        + "\n\n[V4.10.1: 并发证据 context 已达到总长度上限]"
                    )

                print(
                    "V4.10.1 ANALYZE 最终输入长度 =",
                    len(analysis_source)
                )

                # V6.1:
                # 根据目标文件类型选择分析模式。
                #
                # .py 使用 Python 功能/逻辑审查。
                # C/C++ 保留 V4.10.1 原有并发审查。
                analysis_file = ""

                if self.state.get("target_files"):
                    analysis_file = str(
                        self.state["target_files"][0]
                    )

                elif self.state.get("target_file"):
                    analysis_file = str(
                        self.state["target_file"]
                    )

                if analysis_file.lower().endswith(".py"):

                    analysis_prompt = f"""
请对下面提供的 Python 源码进行严格的功能和逻辑审查。

源码:
{analysis_source}

请重点检查真实源码中的明确问题，包括：

1. 函数实现、参数和调用方式是否存在源码可以直接证明的矛盾。
2. 返回值和计算逻辑是否存在源码可以直接证明的错误。
3. 变量使用是否存在源码可以直接证明的错误。
4. 条件判断、循环和控制流程是否存在源码可以直接证明的错误。
5. 函数调用和参数传递是否存在源码可以直接证明的错误。
6. 是否存在源码可以直接证明的运行时错误。
7. 是否存在可以仅根据提供源码直接证明的逻辑 bug。

特别规则：

- 不能仅根据函数名称推测函数应该执行什么操作。
- 不能把函数名中的 total、sum、count、create、delete 等词直接解释为某种具体实现要求。
- 例如：
  calculate_total(price, quantity)
  return price * quantity
  不能仅因为函数名包含 total，就推断应该执行 price + quantity。
- 如果源码没有提供明确的预期语义、调用约束或其他直接证据，不能因为名称与实现存在语义上的猜测差异而认定为明确问题。
- “名称看起来应该这样”“通常应该这样”“可能应该这样”都不能作为明确问题依据。

如果发现明确问题，必须输出：

Python 审查:

问题:
<明确的问题>

证据:

文件: <源码中的完整文件路径>
行号: <源码中的实际行号>
代码: <直接引用提供源码中的代码>
说明: <根据实际源码明确说明为什么这是错误>

结论:
<明确说明问题、触发方式和后果>

如果没有明确问题：

未发现明确问题

重要要求：

1. 只能使用上面提供的源码作为证据。
2. 文件路径必须来自提供的源码。
3. 行号必须来自提供源码中的实际行号。
4. 代码必须直接来自提供的源码，禁止编造。
5. 必须优先判断实际代码行为，不能只根据函数名称猜测。
6. 如果源码不足以证明问题，不得认定为明确问题。
7. “可能”“推测”“看起来”不能作为明确问题的依据。
8. 只报告明确、可验证的问题。
9. 使用简短中文回答。

只根据给出的源码判断，不要猜测没有提供的代码。
"""

                else:

                    analysis_prompt = f"""
请对下面提供的 C/C++ 源码进行一次严格的并发安全审查。

源码:
{analysis_source}

本次任务重点不是概括文件功能，而是检查真实代码中的并发机制。

请重点检查：

1. std::mutex
2. std::unique_lock
3. std::lock_guard
4. std::scoped_lock
5. std::condition_variable
6. wait / wait_for / wait_until
7. notify_one / notify_all
8. std::thread
9. std::atomic / atomic_flag
10. 所有 lock / unlock 操作
11. 锁保护的数据结构
12. 锁内调用其他函数的情况
13. 多把 mutex 的获取顺序
14. 线程退出和等待逻辑

请特别判断：

- 是否存在同一线程重复获取同一把非递归 mutex 的路径。
- 是否存在不同线程以不同顺序获取多把 mutex 的路径。
- 是否存在持锁期间调用可能再次获取同一把锁的函数。
- condition_variable 是否使用正确的 mutex 和谓词。
- notify 与 wait 的配合是否可能造成永久等待。
- 是否存在锁长期持有或锁内执行阻塞操作。
- 是否存在共享状态没有受到正确同步保护。
- 是否存在明确的数据竞争。
- 是否存在明确的死锁路径。

输出必须严格按照下面格式：

并发审查:

1. mutex:
<实际发现的 mutex 及其保护对象>

2. lock:
<实际发现的加锁方式、作用范围以及关键调用>

3. condition_variable:
<实际发现的 wait / notify 关系>

4. thread / atomic:
<实际发现的线程和原子同步机制>

5. 锁关系:
<说明关键锁之间是否存在调用关系或获取顺序>

证据:

如果发现明确问题，必须输出：

文件: <源码中的完整文件路径>
行号: <源码中的实际行号>
代码: <直接引用提供源码中的代码>
说明: <明确说明为什么这段代码构成并发风险>

结论:
<明确说明问题、触发路径和后果>

如果没有明确问题：

未发现明确问题

重要要求：

1. 只能使用上面提供的源码作为证据。
2. 文件路径必须来自提供的源码。
3. 行号必须来自提供源码中的实际行号。
4. 代码必须直接来自提供的源码，禁止编造。
5. 如果源码不足以证明问题，不得认定为明确问题。
6. “可能”“推测”“看起来”不能作为明确问题的依据。
7. 不要因为代码使用 mutex 就直接认为代码正确。
8. 必须检查锁的实际作用范围和调用关系。
9. 不要只总结文件功能。
10. 使用简短中文回答。

只根据给出的源码判断，不要猜测没有提供的代码。
"""

                analysis_result = self.ask_llm(
                    analysis_prompt
                )


                print(
                "DEBUG: LLM分析完成，长度=",
                len(str(analysis_result))
                )


                self.state["analysis_result"] = analysis_result


                # V5.4:
                # 在 V4 原有 Qwen 分析完成后，
                # 使用同一份 analysis_source 交给 Codex。
                #
                # Codex 是独立第二分析通道：
                # 1. 不覆盖 analysis_result
                # 2. 不修改 V4 ask_llm()
                # 3. 不调用工具
                # 4. 不修改代码
                #
                # 当前阶段只保存 Codex 独立分析结果，
                # 暂不让 Codex 改变状态机判断。

                # V6.1：
                # Qwen 分析完成后，使用同一份 analysis_prompt
                # 交给 Codex 做独立第二分析。
                #
                # Codex 失败时 ask_codex() 返回空字符串，
                # 不阻断 V4 主流程。

                # V6 当前阶段：
                # Codex 尚未接入运行流程。
                # 保留状态字段，但不调用 Codex。
                self.state["codex_analysis"] = ""
                self.state["codex_analyze_done"] = False


                self.state["analyze_done"] = True
                self.state["verify_done"] = False

                print("========== VERIFY 阶段 ==========")

                # V4.5.1:
                # 先建立独立 VERIFY 状态。
                # 当前版本暂不增加新的工具调用，
                # 只验证状态机能够稳定经过 VERIFY。
                self.state["phase"] = "VERIFY"

                self.state["summary_done"] = False

                # V4.5.5:
                # ANALYZE 完成后立即停止当前轮。
                # SUMMARY 生成必须等待下一轮 VERIFY 通过。
                continue


            if tool == "search_code_index":

                self.state["search_done"] = True

                # V6.0:
                # 用户明确提供存在的绝对源码文件时，
                # 不依赖项目索引，直接进入 READ。
                task_text = str(question)

                path_match = re.search(
                    r"(/[^\s]+\.(?:py|cpp|cc|c|h|hpp))",
                    task_text
                )

                if path_match:
                    candidate = path_match.group(1)

                    if os.path.isfile(candidate):
                        self.state["target_files"] = [candidate]
                        self.state["target_file"] = candidate
                        self.state["read_index"] = 0
                        self.state["analysis_context"] = []
                        self.state["read_done"] = False

                        print(
                            "V6.0 exact file bypass:",
                            candidate
                        )

                        self.state["phase"] = "READ"
                        continue

                try:
                    import json

                    if isinstance(result, str):
                        data = json.loads(result)
                    else:
                        data = result


                    best_file = ""
                    best_score = -1

                    ranked_files = []


                    for item in data:

                        file_path = item.get(
                            "file",
                            ""
                        )

                        if not file_path:
                            continue


                        lower = file_path.lower()

                        score = 0


                        if lower.endswith(".cpp"):
                            score += 20

                        if "/tools/server/" in lower:
                            score += 80

                        if "/src/" in lower:
                            score += 30


                        keywords = [
                            "mutex",
                            "thread",
                            "lock",
                            "queue",
                            "server",
                            "scheduler",
                            "worker",
                        ]


                        for k in keywords:

                            if k in lower:
                                score += 15


                        bad = [
                            "examples",
                            "perplexity",
                            "test",
                            "cmakelists",
                        ]


                        for b in bad:

                            if b in lower:
                                score -= 50


                        ranked_files.append(
                            (score, file_path)
                        )


                        if score > best_score:

                            best_score = score
                            best_file = file_path


                    # V5.19:
                    # 如果用户任务中明确指定了源码文件，
                    # 优先使用该文件，不能让文件名 scoring
                    # 把用户明确指定的目标替换成其他候选文件。
                    exact_target_file = ""

                    task_text = str(question)

                    path_match = re.search(
                        r"(/[^\s]+\.(?:py|cpp|cc|c|h|hpp))",
                        task_text
                    )

                    if path_match:
                        candidate = path_match.group(1)

                        if os.path.isfile(candidate):
                            exact_target_file = candidate

                    # V4.9.1:
                    # 保存 SEARCH 阶段排名最高的 Top 5 候选文件。
                    ranked_files.sort(
                        key=lambda x: x[0],
                        reverse=True
                    )

                    if exact_target_file:
                        self.state["target_files"] = [
                            exact_target_file
                        ]

                        print(
                            "V5.19 exact target:",
                            exact_target_file
                        )

                    else:
                        self.state["target_files"] = [
                            file_path
                            for score, file_path
                            in ranked_files[:5]
                        ]

                    # V5.11.1:
                    # SEARCH 已返回候选文件时，直接使用第一个候选。
                    if not best_file and self.state["target_files"]:
                        best_file = self.state["target_files"][0]

                        print(
                            "V5.11.1 direct candidate fallback:",
                            best_file
                        )


                    # V4.9.2：
                    # 每次新的 SEARCH 都从第一个候选文件重新读取。
                    self.state["read_index"] = 0
                    self.state["analysis_context"] = []
                    self.state["read_done"] = False

                    print(
                        "V4.9.1 candidate files:",
                        self.state["target_files"]
                    )


                    self.state["target_file"] = best_file


                    print(
                        "V4.6.3 ranked target:",
                        best_file,
                        "score:",
                        best_score
                    )


                    if self.state["target_file"]:

                        self.state["phase"] = "READ"


                except Exception as e:

                    print(
                        "解析搜索结果失败:",
                        e
                    )

                    # V5.11:
                    # SEARCH 解析失败时，如果用户提供的是明确文件路径，
                    # 直接进入 READ，避免无意义重复 SEARCH。
                    fallback_file = ""

                    # V5.11 fix:
                    # run() 的用户任务实际来自 question。
                    # SEARCH 解析失败时直接使用当前 question
                    # 做明确文件路径 fallback。
                    task_text = str(question)

                    path_match = re.search(
                        r"(/[^\s]+\.(?:py|cpp|cc|c|h|hpp))",
                        task_text
                    )

                    if path_match:
                        candidate = path_match.group(1)

                        if os.path.isfile(candidate):
                            fallback_file = candidate

                    if fallback_file:

                        self.state["target_files"] = [
                            fallback_file
                        ]

                        self.state["target_file"] = fallback_file
                        self.state["read_index"] = 0
                        self.state["analysis_context"] = []
                        self.state["read_done"] = False

                        print(
                            "V5.11 SEARCH fallback:",
                            fallback_file
                        )

                        self.state["phase"] = "READ"

                    else:
                        print(
                            "V6.1.9 SEARCH 无结果，停止重复搜索"
                        )

                        self.state["summary_done"] = True
                        self.state["phase"] = "SUMMARY"


            # V4.9.3:
            # 移除 V4.7.3 的单文件 target_file 强制覆盖逻辑。
            #
            # V4.9.2 起，READ 路径唯一由：
            #     target_files[read_index]
            # 决定。
            #
            # 不允许旧版 target_file 保护逻辑覆盖当前候选文件。

            MAX_OBSERVATION_CHARS = 3500

            if tool == "read_file_chunk":

                observation = str(result)

                if len(observation) > MAX_OBSERVATION_CHARS:
                    observation = (
                        observation[:MAX_OBSERVATION_CHARS]
                        + "\n\n[工具结果已截断]"
                    )

                continue



            observation = str(result)

            if len(observation) > MAX_OBSERVATION_CHARS:
                observation = (
                    observation[:MAX_OBSERVATION_CHARS]
                    + "\n\n[工具结果已截断]"
                )
