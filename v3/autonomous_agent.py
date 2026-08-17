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

            # V4.9.1: 保存 SEARCH 阶段发现的多个候选文件。
            # target_file 继续保留，兼容 V4.8.1 当前单文件流程。
            "target_files": [],

            # V4.9.2: 当前正在读取的候选文件索引。
            "read_index": 0,

            "read_done": False,

            "analysis_context": [], 

            "analyze_done": False,

            "verify_done": False,

            "summary_done": False,

            "can_modify": False,

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
            else ""
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
            "锁顺序",
            "等待自身",
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

        # 如果结论声称存在明确缺陷，但证据中没有发现
        # 对应的错误机制，则降级。
        if has_strong_claim and not has_mechanism:
            print(
                "DEBUG: SUMMARY Validator 检测到"
                "“明确问题”缺少证据机制"
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

                import re

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

                no_clear_problem = (
                    "未发现明确问题" in analysis_text
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

                    self.state["phase"] = "SUMMARY"

                else:

                    print(
                        "VERIFY: 分析结果状态检查失败"
                    )

                    print(
                        "VERIFY: 回退 ANALYZE"
                    )

                    self.state["verify_done"] = False

                    # VERIFY 未通过时绝对禁止修改。
                    self.state["can_modify"] = False

                    self.state["analyze_done"] = False
                    self.state["phase"] = "ANALYZE"

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

重要规则：
1. 原始代码分析结果是最高优先级证据。
2. 分块分析结果只是辅助理解，不能覆盖原始代码中的事实。
3. 如果分块分析与原始代码分析结果冲突，以原始代码分析结果为准。
4. 不得根据常识补充源码中没有出现的行为。
5. 不得把“可能存在”写成“已经存在”。
6. 如果无法从代码中确认问题，必须写“未发现明确问题”。

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


            response = self.ask_llm(
                prompt
            )


            print(response)


            tool, args = self.extract_tool(
                response
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

                    args = f"{target}|1|100"

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

                continue


            print(
                "执行工具:",
                tool
            )


            print(
                "参数:",
                args
            )

            # V4.9.2：
            # READ 阶段允许连续读取多个候选文件。
            # 不再使用旧版 read_done 阻止后续 READ。


            result = self.controller.call(
                tool,
                args
            )

            print("DEBUG tool returned:", type(result))
            print("DEBUG result length:", len(str(result)))
            # V4.3.2 reject directory read

            if tool == "read_file_chunk":

                if args.strip() == self.project:

                    print(
                          "禁止读取项目目录"
                    )

                    args = (
                            self.state["target_file"]
                            + "|1|100"
                    )

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
                MAX_ANALYSIS_CHARS = 6000

                # V4.9.2 step3:
                # ANALYZE 不再只分析最后一次 READ 的 result。
                # 将 SEARCH 阶段选出的多个候选文件统一送入分析上下文。
                #
                # 每个文件最多保留 1000 字符，
                # 防止多个大型源码文件导致本地模型推理时间过长。

                analysis_parts = []

                for item in self.state["analysis_context"]:

                    file_path = item.get("file", "")
                    source = str(item.get("source", ""))

                    source = source[:1000]

                    analysis_parts.append(
                        f"===== 文件: {file_path} =====\n"
                        + source
                    )

                analysis_source = "\n\n".join(
                    analysis_parts
                )

                print(
                    "V4.9.2 ANALYZE 文件数量 =",
                    len(analysis_parts)
                )

                print(
                    "DEBUG: ANALYZE 原始输入长度 =",
                    len(analysis_source)
                )

                if len(analysis_source) > MAX_ANALYSIS_CHARS:

                    analysis_source = (
                        analysis_source[:MAX_ANALYSIS_CHARS]
                        + "\n\n[源码已由 V4.7.1 截断]"
                    )

                    print(
                        "DEBUG: ANALYZE 输入已压缩为 =",
                        len(analysis_source)
                    )

                analysis_prompt = f"""
请分析下面的 C/C++ 源码。

源码:
{analysis_source}

请简洁回答：

1. 文件职责
2. 核心数据结构
3. 关键流程
4. mutex / lock / 线程并发机制
5. 是否存在明确的并发风险

如果发现明确问题，必须严格按照下面格式输出：

证据:
文件: <源码中的完整文件路径>
行号: <源码中的实际行号>
代码: <从提供源码中直接引用的代码>
说明: <说明这段代码为什么构成并发风险>

结论:
<明确说明存在什么问题，以及为什么可能形成死锁或其他并发风险>

如果没有明确问题：
未发现明确问题

重要要求：
1. 证据中的文件必须来自上面提供的源码。
2. 行号必须来自源码前面的实际行号。
3. 代码必须直接来自提供的源码，禁止编造。
4. 如果无法从提供的源码确认具体证据，必须输出：
未发现明确问题
5. 不允许仅凭“可能”“推测”“看起来”等模糊描述认定存在问题。

只根据给出的源码判断，不要猜测未提供的代码。
不要重复源码。
使用简短中文回答。
"""

                analysis_result = self.ask_llm(
                    analysis_prompt
                )


                print(
                "DEBUG: LLM分析完成，长度=",
                len(str(analysis_result))
                )


                self.state["analysis_result"] = analysis_result

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


                    # V4.9.1:
                    # 保存 SEARCH 阶段排名最高的 Top 5 候选文件。
                    ranked_files.sort(
                        key=lambda x: x[0],
                        reverse=True
                    )

                    self.state["target_files"] = [
                        file_path
                        for score, file_path
                        in ranked_files[:5]
                    ]

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


            # V4.9.3:
            # 移除 V4.7.3 的单文件 target_file 强制覆盖逻辑。
            #
            # V4.9.2 起，READ 路径唯一由：
            #     target_files[read_index]
            # 决定。
            #
            # 不允许旧版 target_file 保护逻辑覆盖当前候选文件。

            if tool == "read_file_chunk":

                observation = str(result)

                continue



            observation = str(result) 
