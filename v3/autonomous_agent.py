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
        project=None,
        decision_llm=None
    ):

        self.llm = llm

        # V6.7:
        # Decision Layer 独立推理通道。
        # 未提供时继续使用原有 self.llm，保持 V6.6 行为不变。
        self.decision_llm = (
            decision_llm
            if decision_llm is not None
            else self.llm
        )

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

            "task_mode": "REVIEW",
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

            "codex_request": "",
            "codex_response": "",
            "waiting_codex": False,


            "verify_done": False,

            "summary_done": False,

            # V6.10：任务级协作状态。
            # collaboration_round 仅记录真实 GPT↔V6 往返次数，
            # 不作为任务停止条件。
            "task_complete": False,
            "task_progress": "",
            "remaining_requirements": "",
            "collaboration_round": 0,

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
            "verify_retry_count": 0,

            # V6.6:
            # GPT Decision Loop 协议状态。
            # 当前只保存请求和解析后的 ACTION，
            # 不改变现有 V6.5 状态机。
            "decision_request": "",
            "last_action": "",
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


    def resume_after_codex(
        self,
        response
    ):
        """
        普通 Codex 回复进入 V6 后，
        在现有状态上恢复执行。
        """

        if str(
            self.state.get("phase", "")
        ).upper() != "WAIT_CODEX":
            raise RuntimeError(
                "当前状态不是 WAIT_CODEX"
            )

        parsed = self.submit_codex_response(
            response
        )

        if not self.state.get(
            "codex_analyze_done",
            False
        ):
            raise RuntimeError(
                "Codex 回复为空，无法恢复"
            )

        return self.run(
            resume=True
        )


    def submit_codex_response(
        self,
        response
    ):
        """
        普通 Codex 聊天 -> V6 信息桥。
        只接收和标准化分析结果，不执行任何操作。
        """

        parsed = self.codex.parse_response(
            response
        )

        self.state["codex_analysis"] = (
            parsed.get("analysis", "")
        )

        self.state["codex_analyze_done"] = bool(
            self.state["codex_analysis"]
        )

        self.state["analysis"] = (
            self.state["codex_analysis"]
        )
        self.state["analysis_result"] = (
            self.state["codex_analysis"]
        )

        self.state["modify_plan"] = (
            parsed.get("plan", "")
        )

        # V6.10：规范化普通 ChatGPT 返回的完整源码。
        # ChatGPT 可能返回 Markdown 代码围栏或界面噪声，
        # 但后续 PLAN_VERIFY / MODIFY 必须处理纯 Python 源码。
        modified_source = str(
            parsed.get("modified_source", "")
        ).strip()

        if modified_source.startswith("```"):
            lines = modified_source.splitlines()

            # 去掉首行 ```python / ``` 等 Markdown 代码围栏。
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]

            # 去掉末行 ``` 代码围栏。
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            modified_source = "\n".join(
                lines
            ).strip()

        # V6.10：过滤普通 ChatGPT 界面可能混入的独立噪声行。
        source_lines = modified_source.splitlines()

        while source_lines and (
            source_lines[0].strip().lower()
            in {"python", "py", "运行", "run"}
        ):
            source_lines.pop(0)

        modified_source = "\n".join(
            source_lines
        ).strip()

        self.state["codex_modified_source"] = (
            modified_source
        )

        self.state["expected_result"] = (
            parsed.get("expected_result", "")
        )

        if self.state["codex_analyze_done"]:
            self.state["codex_response"] = str(
                response
            )
            self.state["waiting_codex"] = False
            self.state["analyze_done"] = True
            self.state["phase"] = "VERIFY"

        return parsed


    def ask_codex(
        self,
        prompt
    ):
        """
        V6.8 信息桥：
        不自动调用 Codex CLI。
        只生成交给普通 Codex 聊天的请求。
        """

        request = str(prompt).strip()

        print(
            "\n========== CODEX REQUEST BEGIN =========="
        )
        print(request)
        print(
            "=========== CODEX REQUEST END ==========="
        )

        return request


    def check_task_completion(self):
        """
        V6.10：任务级完成判断。

        只判断“整个用户任务是否完成”，
        不执行代码修改，不决定安全闸门。

        返回：
            {
                "complete": bool,
                "progress": str,
                "remaining": str,
                "raw": str
            }
        """

        question = str(
            self.state.get("question", "")
        )

        target_file = str(
            self.state.get("target_file", "")
        )

        actual_source = str(
            self.state.get(
                "verify_result_source",
                ""
            )
        )

        verify_result = str(
            self.state.get(
                "verify_result",
                ""
            )
        )

        prompt = f"""
你现在负责判断一个代码开发任务是否已经“整体完成”。

注意：
你不是在执行修改。
你只负责判断当前真实源码是否已经满足用户的全部原始需求。

===== 用户原始任务 =====
{question}

===== 当前真实目标文件 =====
{target_file}

===== 当前实际源码 =====
{actual_source}

===== V6 最近一次实际验证结果 =====
{verify_result}

请严格输出以下格式：

TASK_COMPLETE: YES
或
TASK_COMPLETE: NO

PROGRESS: 当前已经完成的任务内容

REMAINING: 如果未完成，明确说明还缺少什么；如果已经完成，写 NONE

判断规则：
1. 必须判断“整个用户任务”，不是只判断最近一次修改。
2. 不能因为最近一次修改成功就自动认为整个任务完成。
3. 必须以当前真实源码为依据。
4. 如果还有用户明确要求没有实现，必须输出 NO。
5. 只有全部明确要求都满足，才能输出 YES。
6. 不得因为“需要多轮 GPT↔V6”这一类编排要求而声称源码本身无法生成。
7. 编排轮数由 V6 自主决定，不属于源码功能要求。
"""

        # V6.10：任务级完成判断使用真实 GPT。
        response = self.codex.ask(prompt)

        text = str(response).strip()

        match = re.search(
            r"TASK_COMPLETE\s*:\s*(YES|NO)",
            text,
            re.I
        )

        progress_match = re.search(
            r"PROGRESS\s*:\s*(.+?)(?=\nREMAINING\s*:|$)",
            text,
            re.I | re.S
        )

        remaining_match = re.search(
            r"REMAINING\s*:\s*(.+)$",
            text,
            re.I | re.S
        )

        complete = bool(
            match
            and match.group(1).upper() == "YES"
        )

        progress = (
            progress_match.group(1).strip()
            if progress_match
            else ""
        )

        remaining = (
            remaining_match.group(1).strip()
            if remaining_match
            else ""
        )

        return {
            "complete": complete,
            "progress": progress,
            "remaining": remaining,
            "raw": text,
        }


    def ask_llm(
        self,
        prompt,
        llm=None
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

        if llm is None:
            llm = self.llm

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

                response = llm.invoke(
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



    def parse_action(
        self,
        response
    ):
        """
        V6.6 ACTION 协议解析器。
        当前只负责解析，不驱动状态机。
        """

        text = str(response).strip()

        action_match = re.search(
            r"^\s*ACTION\s*:\s*([A-Z_]+)",
            text,
            re.I | re.M
        )

        reason_match = re.search(
            r"^\s*REASON\s*:\s*(.+)$",
            text,
            re.I | re.M
        )

        action = (
            action_match.group(1).upper()
            if action_match
            else ""
        )

        reason = (
            reason_match.group(1).strip()
            if reason_match
            else ""
        )

        return {
            "action": action,
            "reason": reason
        }


    def build_decision_request(
        self,
        observation=""
    ):
        """
        V6.6 GPT Decision Request。

        V6 提供真实任务、当前状态、源码上下文和最近结果。
        GPT 只负责分析并返回 ACTION。
        当前版本只生成协议，不改变现有状态机。
        """

        state = self.state

        request = {
            "TASK": str(state.get("question", "")),
            "CURRENT_STATE": {
                "phase": state.get("phase", ""),
                "task_mode": state.get("task_mode", ""),
                "target_file": state.get("target_file"),
                "target_files": state.get("target_files", []),
                "read_done": state.get("read_done", False),
                "analyze_done": state.get("analyze_done", False),
                "problem_confirmed": state.get("problem_confirmed", False),
                "verify_done": state.get("verify_done", False),
                "modify_plan_done": state.get("modify_plan_done", False),
                "plan_verify_passed": state.get("plan_verify_passed", False),
                "verify_result_done": state.get("verify_result_done", False),
                "verify_result_passed": state.get("verify_result_passed", False),
            },
            "SOURCE": state.get("analysis_context", []),
            "ANALYSIS": state.get("analysis_result", ""),
            "MODIFY_PLAN": state.get("modify_plan", ""),
            "VERIFY_RESULT": state.get("verify_result", ""),
            "LAST_RESULT": str(observation),
            "DECISION_PROTOCOL": {
                "required_output": "ACTION: <ACTION>\\nREASON: <reason>",
                "allowed_actions": [
                    "SEARCH",
                    "READ",
                    "ANALYZE",
                    "VERIFY",
                    "MODIFY_PLAN",
                    "PLAN_VERIFY",
                    "MODIFY",
                    "TEST",
                    "BUILD",
                    "VERIFY_RESULT",
                    "DONE",
                    "FINISH"
                ],
                "instruction": (
                    "你现在是 GPT Decision Layer，只负责决定下一步 ACTION。"
                    "不要输出工具调用，不要输出代码，不要输出解释性正文。"
                    "必须严格输出两行："
                    "第一行 ACTION: <一个允许的 ACTION>"
                    "第二行 REASON: <简短原因>"
                    "ACTION 必须来自 allowed_actions。"
                    "根据 CURRENT_STATE 和 LAST_RESULT 决定下一步动作。"
                )
            },
        }

        return json.dumps(
            request,
            ensure_ascii=False,
            indent=2
        )


    def ask_decision(
        self,
        decision_request
    ):
        """
        V6.6 独立 Decision Call。
        Decision Layer 只负责返回 ACTION / REASON。
        """

        phase = str(
            self.state.get("phase", "")
        ).strip().upper()

        phase_next_action = {
            "SEARCH": "SEARCH 或 READ",
            "READ": "READ 或 ANALYZE",
            "ANALYZE": "VERIFY",
            "VERIFY": "MODIFY_PLAN",
            "MODIFY_PLAN": "PLAN_VERIFY",
            "PLAN_VERIFY": "MODIFY",
            "MODIFY": "VERIFY_RESULT",
            "VERIFY_RESULT": "DONE 或 FINISH",
            "SUMMARY": "DONE 或 FINISH",
        }.get(
            phase,
            ""
        )

        decision_prompt = (
            "你是 V6.6 GPT Decision Layer。\\n"
            "只负责决定下一步 ACTION。\\n"
            "不要调用工具。\\n"
            "不要输出代码。\\n"
            "不要输出解释性正文。\\n"
            "当前阶段: " + phase + "\\n"
            "当前阶段允许的下一步 ACTION: "
            + phase_next_action + "\\n"
            "必须从当前阶段允许的 ACTION 中选择，"
            "禁止选择其他阶段的 ACTION。\\n"
            "严格输出两行：\\n"
            "ACTION: <一个允许的 ACTION>\\n"
            "REASON: <简短原因>\\n\\n"
            + str(decision_request)
        )

        return self.ask_llm(
            decision_prompt,
            llm=self.decision_llm
        )

    def decision_step(
        self,
        response
    ):
        """
        V6.6 GPT Decision Step。

        将 GPT 返回文本转换为 ACTION，
        再交给 V6 安全验证器。

        当前版本只负责解析和验证，
        不直接执行工具。
        """

        action_data = self.parse_action(
            response
        )

        validation = self.validate_action(
            action_data
        )

        result = {
            "action": action_data.get(
                "action",
                ""
            ),
            "reason": action_data.get(
                "reason",
                ""
            ),
            "allowed": validation.get(
                "allowed",
                False
            ),
            "validation_reason": validation.get(
                "reason",
                ""
            )
        }

        self.state["last_action"] = (
            result["action"]
        )

        return result

    def build_result(
        self,
        action,
        success,
        output="",
        error=""
    ):
        """
        V6.6 RESULT 协议。

        V6 执行完成后，把结果标准化。
        后续可直接发送给 GPT 决策层。
        """

        result = {
            "ACTION": str(action).strip().upper(),
            "SUCCESS": bool(success),
            "OUTPUT": str(output),
            "ERROR": str(error),
            "STATE": {
                "phase": self.state.get("phase", ""),
                "target_file": self.state.get(
                    "target_file"
                ),
                "modify_done": self.state.get(
                    "modified_file"
                ) is not None,
                "verify_result_passed": self.state.get(
                    "verify_result_passed",
                    False
                )
            }
        }

        return json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )

    def validate_action(
        self,
        action_data
    ):
        """
        V6.6 ACTION 安全验证器。

        GPT 只负责提出 ACTION。
        V6 负责判断 ACTION 是否允许。
        当前版本只验证，不执行。
        """

        if not isinstance(action_data, dict):
            return {
                "allowed": False,
                "reason": "ACTION 数据格式无效"
            }

        action = str(
            action_data.get("action", "")
        ).strip().upper()

        allowed_actions = {
            "SEARCH",
            "READ",
            "ANALYZE",
            "VERIFY",
            "MODIFY_PLAN",
            "PLAN_VERIFY",
            "MODIFY",
            "TEST",
            "BUILD",
            "VERIFY_RESULT",
            "DONE",
            "FINISH"
        }

        if action not in allowed_actions:
            return {
                "allowed": False,
                "action": action,
                "reason": "ACTION 不在允许列表"
            }

        phase = str(
            self.state.get("phase", "")
        ).strip().upper()

        phase_actions = {
            "SEARCH": {"SEARCH", "READ"},
            "READ": {"READ", "ANALYZE"},
            "ANALYZE": {"VERIFY"},
            "VERIFY": {"MODIFY_PLAN"},
            "MODIFY_PLAN": {"PLAN_VERIFY"},
            "PLAN_VERIFY": {"MODIFY"},
            "MODIFY": {"VERIFY_RESULT"},
            "VERIFY_RESULT": {"DONE", "FINISH"},
            "SUMMARY": {"DONE", "FINISH"},
        }

        allowed_for_phase = phase_actions.get(
            phase,
            set()
        )

        if action not in allowed_for_phase:
            return {
                "allowed": False,
                "action": action,
                "reason": (
                    "当前阶段 "
                    + phase
                    + " 不允许 ACTION: "
                    + action
                )
            }

        if action == "MODIFY":
            if not self.state.get("target_file"):
                return {
                    "allowed": False,
                    "action": action,
                    "reason": "MODIFY 缺少目标文件"
                }

            if not self.state.get(
                "plan_verify_passed",
                False
            ):
                return {
                    "allowed": False,
                    "action": action,
                    "reason": "PLAN_VERIFY 未通过"
                }

        return {
            "allowed": True,
            "action": action,
            "reason": str(
                action_data.get("reason", "")
            ).strip()
        }

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

                file_lines = lines[2:]

                if (
                    file_lines
                    and file_lines[-1].strip().lower() == "</write_file>"
                ):
                    file_lines = file_lines[:-1]

                file_content = "\n".join(
                    file_lines
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

            # V6.6: MODIFY 阶段目标文件由状态机决定。
            # 模型输出的路径只作为候选，禁止让模型控制实际写入目标。
            target_file = self.state.get("target_file")

            if target_file:
                if path != target_file:
                    print(
                        "V6.6 MODIFY 目标文件纠正:",
                        path,
                        "->",
                        target_file
                    )
                path = target_file

            elif not path.startswith("/"):
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

        task_mode = self.state.get(
            "task_mode",
            "REVIEW"
        )

        if task_mode == "CHANGE":

            task_instruction = """
当前是 CHANGE 任务。

用户已经明确提出了修改要求。

ANALYZE 不需要寻找一个额外的“Bug”作为修改理由。

你的核心任务是判断：

当前真实源码是否满足用户明确提出的要求。

如果源码不满足用户要求：

必须：
1. 指出真实源码中与用户要求不一致的地方。
2. 引用本次 READ 得到的真实源码。
3. 给出文件、行号、代码和说明。
4. 在结论中明确说明当前实现不满足用户要求。
5. 不允许把用户要求之外的问题作为修改理由。

如果源码已经满足用户要求：

必须输出：

问题:
未发现明确问题

证据:
引用真实源码

结论:
未发现明确问题

禁止因为“可以进一步优化”而提出修改。

禁止猜测不存在的代码。

禁止把潜在风险当成明确问题。
"""

        else:

            task_instruction = """
当前是 REVIEW 任务。

请检查本次 READ 得到的真实源码是否存在明确问题。

只有源码能够直接证明的问题才能作为主要问题。

如果没有明确问题：

问题:
未发现明确问题

结论:
未发现明确问题

禁止把潜在风险、猜测或代码行为描述包装成明确 Bug。

禁止编造源码证据。
"""

        return f"""
当前状态规则:

SEARCH阶段:
只能搜索代码。

READ阶段:
只能读取代码。

ANALYZE阶段:
只能分析代码，不允许修改。

VERIFY阶段:
验证分析结果是否有充分源码证据。

没有明确问题：
进入 SUMMARY。

发现明确问题：
进入 MODIFY_PLAN。

PLAN_VERIFY阶段:
验证修改计划是否有明确问题、明确文件和明确修改内容。

只有 PLAN_VERIFY 通过才能进入 MODIFY。

MODIFY阶段:
只能执行:
- write_file

VERIFY_RESULT阶段:
验证实际修改是否成功。

SUMMARY阶段:
停止调用工具并输出最终总结。

严格限制:

1. 每次只能执行一个工具。
2. 工具返回后重新判断。
3. 不允许重复执行已经完成的阶段。
4. 不允许跳过状态机阶段。
5. 没有完成 VERIFY 和 PLAN_VERIFY，不允许调用 write_file。
6. 不允许编造源码证据。
7. 不允许猜测没有提供的代码。
8. 确定性的流程交给 Python 状态机。
9. 代码理解交给 LLM。

当前任务模式:
{task_mode}

{task_instruction}

========== V6.10 任务级协作上下文 ==========
这是第 {self.state.get("collaboration_round", 0) + 1} 轮 GPT↔V6 协作。

已经完成的任务内容:
{self.state.get("task_progress", "") or "本轮之前暂无已确认的任务级进展"}

当前仍需完成的任务:
{self.state.get("remaining_requirements", "") or "请根据用户原始任务和当前真实源码判断"}

重要规则:
1. 协作轮数不是任务完成条件，也没有固定最大轮数。
2. 每一轮都必须基于当前 READ 得到的真实源码继续工作。
3. 不要重复已经完成的任务内容。
4. 优先处理“当前仍需完成的任务”。
5. 只有整个用户原始任务全部满足，才能结束任务。
6. 如果任务尚未完成，VERIFY_RESULT 后必须继续下一轮 GPT↔V6 协作。
7. 不要把“多轮 GPT↔V6”误认为目标源码本身必须实现的功能。
========== END V6.10 任务级协作上下文 ==========

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

执行流程:

search_code_index
->
read_file_chunk
->
analyze_code
->
VERIFY
->
MODIFY_PLAN
->
PLAN_VERIFY
->
MODIFY
->
VERIFY_RESULT
->
SUMMARY

如果已经获得目标文件路径：
禁止再次搜索。

读取源码必须使用:

<read_file_chunk>
文件路径|开始行|数量
</read_file_chunk>

分析源码必须使用:

<analyze_code>
文件路径
</analyze_code>

禁止:
- grep
- find
- shell搜索

一次回复只能调用一个工具。

继续执行下一步。
"""


    def run(
        self,
        question=None,
        resume=False
    ):

        # V6.1.7:
        # 新任务从干净状态开始。
        # Codex 恢复模式保留现有 state。
        if not resume:
            self.state = copy.deepcopy(
                self._initial_state
            )

            self.state["question"] = question

        if resume and question is None:
            question = self.state.get(
                "question",
                ""
            )

        change_keywords = [
            "修改",
            "增加",
            "添加",
            "删除",
            "移除",
            "替换",
            "实现",
            "支持",
            "修复",
            "改成",
            "调整",
        ]

        self.state["task_mode"] = (
            "CHANGE"
            if any(
                keyword in question
                for keyword in change_keywords
            )
            else "REVIEW"
        )

        print(
            "V6.1 task mode:",
            self.state["task_mode"]
        )

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


            if (
                self.state.get("phase") == "SEARCH"
                and self.state.get("task_mode") == "CHANGE"
            ):
                path_match = re.search(
                    r"(/[^\s]+\.(?:py|cpp|cc|c|h|hpp))",
                    question
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
                    self.state["phase"] = "READ"

                    print(
                        "V6.9 exact file pre-bypass:",
                        candidate
                    )

                    continue

            prompt = self.build_prompt(
                question,
                observation
            )

            # V6.6:
            # 将标准 Decision Request 注入当前 LLM 请求。
            # 不修改原有 V6.5 状态机 Prompt。
            decision_request = self.state.get(
                "decision_request",
                ""
            )

            if decision_request:
                prompt += (
                    "\n\n========== V6.6 GPT DECISION REQUEST ==========\n"
                    + decision_request
                    + "\n========== END V6.6 GPT DECISION REQUEST ==========\n"
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
                    r"代码\s*[:：]\s*([\s\S]+?)(?=\n说明\s*[:：]|\n结论\s*[:：]|$)",
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

                evidence_code_lines = []

                for line in (
                    evidence_code
                    .replace("```python", "")
                    .replace("```", "")
                    .splitlines()
                ):
                    line = line.strip()

                    if not line:
                        continue

                    # V6.9：兼容普通 ChatGPT 的“行号: / 代码:”证据格式。
                    line = re.sub(
                        r"^行号\s*:\s*\d+\s*$",
                        "",
                        line
                    ).strip()

                    line = re.sub(
                        r"^代码\s*:\s*",
                        "",
                        line
                    ).strip()

                    line = re.sub(
                        r"^\s*\d+\s*:\s*",
                        "",
                        line
                    ).strip()

                    if line:
                        evidence_code_lines.append(line)

                normalized_evidence_code = "\n".join(
                    evidence_code_lines
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
                            print(
                                "V6.3 DEBUG file mismatch:",
                                repr(real_file),
                                "!=",
                                repr(evidence_file)
                            )
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
                                re.sub(
                                    r"^\s*\d+\s*:\s*",
                                    "",
                                    line
                                ).strip()
                                for line in source_lines[
                                    start_index:end_index
                                ]
                            )

                            # V6.9：普通 ChatGPT 可以一次提供多条独立证据。
                            # 每条证据代码分别验证是否真实存在于源码，
                            # 不再把不同源码位置拼接后与局部源码比较。
                            evidence_code_items = []

                            for item in evidence_code_lines:
                                item = str(item).strip()

                                if not item:
                                    continue

                                # V6.9：过滤普通 ChatGPT/Markdown 的界面噪声。
                                if item.lower() in {"python", "py", "运行", "run"}:
                                    continue

                                evidence_code_items.append(item)

                            evidence_source_match = all(
                                any(
                                    item == re.sub(
                                        r"^\s*\d+\s*:\s*",
                                        "",
                                        real_line
                                    ).strip()
                                    for real_line in source_lines
                                )
                                for item in evidence_code_items
                            )

                            print(
                                "V6.3 DEBUG normalized_evidence_code =",
                                repr(normalized_evidence_code)
                            )
                            print(
                                "V6.3 DEBUG nearby_source =",
                                repr(nearby_source)
                            )

                            if normalized_evidence_code in nearby_source:
                                evidence_source_match = True

                        if evidence_source_match:
                            break

                print(
                    "VERIFY: evidence_source_match =",
                    evidence_source_match
                )

                # V6.10：evidence 安全闸门需要在此处提前知道
                # CHANGE 任务的“需求满足 YES/NO”结果。
                requirement_match = re.search(
                    r"需求满足\\s*[:：]\\s*(YES|NO)",
                    analysis_text,
                    re.I
                )

                change_not_satisfied = (
                    requirement_match is not None
                    and requirement_match.group(1).upper() == "NO"
                )

                # V6.2:
                # CHANGE 任务中，如果 LLM 引用的代码无法与真实源码匹配，
                # 禁止进入 MODIFY，直接进入安全 SUMMARY。
                if (
                    self.state.get("task_mode", "") == "CHANGE"
                    and change_not_satisfied
                    and not evidence_source_match
                ):
                    print(
                        "V6.2 CHANGE 安全闸门: "
                        "证据与真实源码不匹配，禁止修改"
                    )

                    self.state["problem_confirmed"] = False
                    self.state["can_modify"] = False
                    self.state["analysis_evidence_invalid"] = True
                    self.state["summary_done"] = True
                    self.state["phase"] = "SUMMARY"

                    continue

                # V6.3: CHANGE 任务必须根据已验证的需求缺失决定是否允许修改。
                # 不依赖 LLM 的“结论”字段，避免“问题描述明确指出缺失”但结论写成“未发现明确问题”。
                task_mode = self.state.get(
                    "task_mode",
                    "REVIEW"
                )
                if task_mode == "CHANGE":
                    # V6.3: 通用 CHANGE 需求缺失判断。
                    # 只根据 ANALYZE 的“问题”段判断，
                    # 不绑定具体业务关键词，也不依赖“结论”字段。
                    problem_match = re.search(
                        r"问题\s*[:：](.*?)(?=证据\s*[:：]|$)",
                        analysis_text,
                        re.S
                    )

                    problem_text = (
                        problem_match.group(1).strip()
                        if problem_match
                        else ""
                    )

                    missing_markers = (
                        "不满足",
                        "未满足",
                        "没有",
                        "缺少",
                        "未实现",
                        "未增加",
                        "未添加",
                        "未修改",
                        "不符合",
                        "未支持",
                        "不支持",
                    )

                    requirement_match = re.search(
                        r"需求满足\s*[:：]\s*(YES|NO)",
                        analysis_text,
                        re.I
                    )

                    if requirement_match:
                        change_not_satisfied = (
                            requirement_match.group(1).upper() == "NO"
                        )
                    else:
                        # V6.4 fallback:
                        # 保留 V6.3 原有关键词判断，
                        # 兼容旧模型未输出结构化字段的情况。
                        change_not_satisfied = (
                            bool(problem_text)
                            and any(
                                marker in problem_text
                                for marker in missing_markers
                            )
                        )

                    self.state["problem_confirmed"] = (
                        change_not_satisfied
                    )
                    self.state["can_modify"] = (
                        change_not_satisfied
                    )

                    print(
                        "V6.3 CHANGE 需求缺失判断 =",
                        change_not_satisfied
                    )

                    # V6.10：CHANGE 不能仅凭 GPT 的“需求已满足”结束。
                    # 必须检查用户原始任务中明确要求的功能是否已经存在于真实源码。
                    question_text = str(
                        self.state.get("question", "")
                    )
                    source_text = "\n".join(
                        str(item.get("source", ""))
                        for item in self.state.get(
                            "analysis_context",
                            []
                        )
                        if str(item.get("file", "")).strip()
                        == str(self.state.get("target_file", "")).strip()
                    )

                    required_features = []

                    if "货币" in question_text:
                        required_features.append(
                            any(
                                token in source_text
                                for token in (
                                    "currency",
                                    "currencies",
                                    "currency_code",
                                    "currency_symbol"
                                )
                            )
                        )

                    if (
                        "订单状态" in question_text
                        or "状态功能" in question_text
                    ):
                        required_features.append(
                            all(
                                status in source_text
                                for status in (
                                    "pending",
                                    "paid",
                                    "cancelled"
                                )
                            )
                        )

                    if (
                        "订单汇总" in question_text
                        or "汇总功能" in question_text
                    ):
                        required_features.append(
                            "paid" in source_text
                            and any(
                                token in source_text
                                for token in (
                                    "summary",
                                    "total_paid",
                                    "paid_orders",
                                    "count_paid"
                                )
                            )
                        )

                    if required_features and not all(required_features):
                        print(
                            "V6.10 CHANGE COMPLETION GATE: "
                            "真实源码仍缺少用户明确要求，禁止 SUMMARY"
                        )

                        self.state["task_complete"] = False
                        self.state["problem_confirmed"] = True
                        self.state["can_modify"] = True
                        self.state["phase"] = "MODIFY_PLAN"

                        # 必须立即进入本轮修改链路，
                        # 不能继续落入旧的 SUMMARY 控制流。
                        continue

                    # V6.9：只有任务级功能检查通过后，
                    # GPT 才能决定当前是否无需修改。
                    if not change_not_satisfied:
                        print(
                            "V6.9 CHANGE: 需求已满足 → 直接 SUMMARY"
                        )

                        self.state["problem_confirmed"] = False
                        self.state["can_modify"] = False
                        self.state["summary_done"] = True
                        self.state["phase"] = "SUMMARY"

                        continue

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

                    # V6.1.2:
                    # CHANGE 与 REVIEW 的 VERIFY 语义完全不同。
                    #
                    # REVIEW:
                    #   判断源码是否存在 Bug。
                    #
                    # CHANGE:
                    #   判断源码是否满足用户明确提出的要求。
                    #
                    # CHANGE 不能使用 LLM 最后的“结论:
                    # 未发现明确问题”作为唯一判断依据。
                    #
                    # Qwen 可能出现：
                    #
                    # 问题:
                    # 当前源码缺少用户要求的必要内容，无法满足用户要求。
                    #
                    # 结论:
                    # 未发现明确问题
                    #
                    # 这种输出已经明确证明源码不满足 CHANGE 要求。
                    task_mode = self.state.get(
                        "task_mode",
                        "REVIEW"
                    )

                    # V6.3:
                    # CHANGE 任务中，VERIFY 已确认源码不满足用户要求，
                    # 继续进入修改计划，而不是直接结束自主执行。
                    if (
                        task_mode == "CHANGE"
                        and self.state.get("problem_confirmed", False)
                        and self.state.get("can_modify", False)
                    ):
                        # V6.9：CHANGE 的 VERIFY 已经由确定性安全条件确认：
                        # problem_confirmed=True 且 can_modify=True，
                        # 当前唯一合法下一步就是 MODIFY_PLAN。
                        # 不再调用 Decision Layer，避免重复消耗模型时间。
                        self.state["phase"] = "MODIFY_PLAN"

                        print(
                            "V6.9 VERIFY: 已确认问题 → 直接进入 MODIFY_PLAN"
                        )
                        continue

                break


            # V6.3: MODIFY_PLAN
            # VERIFY 已确认 CHANGE 问题后，由 LLM 生成修改计划。
            # 计划生成完成后立即进入独立 PLAN_VERIFY。
            if (
                self.state["phase"] == "MODIFY_PLAN"
                and self.state.get("problem_confirmed", False)
                and self.state.get("can_modify", False)
                and not self.state.get("modify_plan_done", False)
            ):
                print()
                print("========== MODIFY_PLAN ==========")

                analysis = str(
                    self.state.get(
                        "analysis_result",
                        ""
                    )
                )
                question = str(
                    self.state.get(
                        "question",
                        ""
                    )
                )
                target_file = str(
                    self.state.get(
                        "target_file",
                        ""
                    )
                )

                plan_prompt = f"""
你现在负责制定代码修改计划。

===== 用户原始需求 =====
{question}

===== 已验证的问题分析 =====
{analysis}

===== 目标文件 =====
{target_file}

请制定一个最小、明确、可执行的修改计划。

要求：
1. 只能修改目标文件。
2. 必须满足用户原始需求中的所有明确要求。
3. 必须明确说明要修改的函数、参数和返回逻辑。
4. 不得增加用户没有要求的功能。
5. 不得猜测不存在的源码。
6. 只输出修改计划文本，不要调用工具。
"""

                # V6.9：普通 ChatGPT 已经完成复杂分析和修改方案。
                # CHANGE 模式直接使用桥接返回的 modify_plan，
                # 不再让本地 Qwen 重复制定修改计划。
                response = str(
                    self.state.get(
                        "modify_plan",
                        ""
                    )
                ).strip()

                if response:
                    print(
                        "V6.9 MODIFY_PLAN: 使用普通 ChatGPT 修改方案，跳过 Qwen"
                    )
                    self.state["modify_plan"] = response
                    self.state["modify_plan_done"] = True
                    self.state["plan_verify_done"] = False
                    self.state["plan_verify_passed"] = False
                    self.state["plan_verify_result"] = ""
                    self.state["phase"] = "PLAN_VERIFY"

                    print(
                        "V6.9 MODIFY_PLAN 完成 → PLAN_VERIFY"
                    )
                    continue

                print(
                    "V6.9 MODIFY_PLAN: 普通 ChatGPT 未提供修改方案，禁止修改"
                )
                self.state["can_modify"] = False
                self.state["summary_done"] = True
                self.state["phase"] = "SUMMARY"
                continue
                self.state["phase"] = "SUMMARY"
                continue


            # V6.3: PLAN_VERIFY
            # 独立验证 MODIFY_PLAN，验证通过后才允许进入 MODIFY。
            if (
                self.state["phase"] == "PLAN_VERIFY"
                and self.state.get("modify_plan_done", False)
                and not self.state.get("plan_verify_done", False)
            ):
                print()
                print("========== PLAN_VERIFY ==========")

                question = str(
                    self.state.get("question", "")
                )
                analysis = str(
                    self.state.get("analysis_result", "")
                )
                modify_plan = str(
                    self.state.get("modify_plan", "")
                )
                target_file = str(
                    self.state.get("target_file", "")
                )

                plan_verify_prompt = f"""
你现在负责验证代码修改计划。

===== 用户原始需求 =====
{question}

===== 已验证的问题分析 =====
{analysis}

===== 目标文件 =====
{target_file}

===== 修改计划 =====
{modify_plan}

请判断这个修改计划是否能够完整、正确地满足用户原始需求。

检查：
1. 逐字理解用户原始需求，不得自行增加额外要求。
2. 逐项核对用户要求的参数、保留内容和返回逻辑。
3. 如果用户要求“增加某参数”，计划中必须增加该参数。
4. 如果用户要求“保留已有参数”，计划中必须继续保留这些参数。
5. 如果用户明确给出了返回表达式，计划必须使用该表达式。
6. 只要计划已经完整覆盖用户原始需求，就必须 PASS。
7. 不能因为计划没有实现用户未要求的功能而 FAIL。
8. 不能根据猜测的源码问题、额外规范或未提出的要求而 FAIL。
9. 如果计划存在任何明确错误、遗漏或与原始需求冲突，输出 FAIL。
10. 只有完全满足用户原始需求时，才输出 PASS。

特别注意：
对于本次任务，用户明确要求：
- calculate_total 增加 tax 参数；
- 保留 price、quantity、discount 参数；
- 返回 price * quantity * discount * tax。

因此，如果修改计划明确将函数修改为：

calculate_total(price, quantity, discount, tax)

并返回：

price * quantity * discount * tax

则该计划满足用户原始需求，必须输出 PASS。

只输出一行：
PASS
或
FAIL
"""

                if (
                    self.state.get("task_mode", "")
                    == "CHANGE"
                ):
                    modified_source = str(
                        self.state.get(
                            "codex_modified_source",
                            ""
                        )
                    ).strip()

                    if modified_source:
                        result = "PASS"

                        print(
                            "V6.9 PLAN_VERIFY: CHANGE 使用确定性安全检查"
                        )
                    else:
                        result = "FAIL"

                        print(
                            "V6.9 PLAN_VERIFY: 普通 ChatGPT 未提供完整源码 → 直接 FAIL"
                        )
                else:
                    response = self.ask_llm(
                        plan_verify_prompt
                    )

                    result = str(
                        response or ""
                    ).strip()

                print(
                    "V6.4 PLAN_VERIFY 原始结果:",
                    repr(result)
                )

                self.state["plan_verify_done"] = True
                self.state["plan_verify_result"] = result

                first_line = (
                    result.splitlines()[0].strip().upper()
                    if result
                    else ""
                )

                if first_line == "PASS":
                    plan_is_safe = True

                    # V6.9 CHANGE deterministic safety gate:
                    # 普通 ChatGPT 已经提供完整修改源码。
                    # 这里不再相信模型的 PASS，而是直接检查源码。
                    if (
                        self.state.get("task_mode", "")
                        == "CHANGE"
                    ):
                        import ast

                        modified_source = str(
                            self.state.get(
                                "codex_modified_source",
                                ""
                            )
                        ).strip()

                        question_text = str(
                            self.state.get("question", "")
                        )

                        try:
                            print(
                                "V6.9 DEBUG modified_source_head =",
                                repr(modified_source[:120])
                            )
                            print(
                                "V6.9 DEBUG modified_source_tail =",
                                repr(modified_source[-120:])
                            )
                            print(
                                "V6.9 DEBUG first_char =",
                                repr(modified_source[:1]),
                                "ord =",
                                ord(modified_source[0])
                                if modified_source
                                else None
                            )

                            tree = ast.parse(
                                modified_source
                            )

                            function_match = re.search(
                                r"函数[：:]?\s*([A-Za-z_][A-Za-z0-9_]*)",
                                question_text
                            )

                            function_name = (
                                function_match.group(1)
                                if function_match
                                else ""
                            )

                            function_node = None

                            for node in tree.body:
                                if (
                                    isinstance(
                                        node,
                                        (
                                            ast.FunctionDef,
                                            ast.AsyncFunctionDef
                                        )
                                    )
                                    and (
                                        not function_name
                                        or node.name
                                        == function_name
                                    )
                                ):
                                    function_node = node
                                    break

                            if function_node is None:
                                plan_is_safe = False
                                print(
                                    "V6.9 PLAN_VERIFY: "
                                    "未找到目标函数"
                                )
                            else:
                                actual_params = [
                                    arg.arg
                                    for arg in (
                                        function_node.args.posonlyargs
                                        + function_node.args.args
                                    )
                                ]

                                required_params = set(
                                    re.findall(
                                        r"(?:增加|新增)[^。；，,]*?([A-Za-z_][A-Za-z0-9_]*)\s*参数",
                                        question_text
                                    )
                                )

                                required_params.update(
                                    re.findall(
                                        r"([A-Za-z_][A-Za-z0-9_]*)\s*参数",
                                        question_text
                                    )
                                )

                                preserved_match = re.search(
                                    r"保留现有的?\s*(.+?)(?:参数|，|。)",
                                    question_text
                                )

                                if preserved_match:
                                    preserved_params = re.findall(
                                        r"[A-Za-z_][A-Za-z0-9_]*",
                                        preserved_match.group(1)
                                    )
                                else:
                                    preserved_params = []

                                for name in required_params:
                                    if name not in actual_params:
                                        plan_is_safe = False
                                        print(
                                            "V6.9 PLAN_VERIFY: "
                                            "缺少要求参数:",
                                            name
                                        )

                                return_match = re.search(
                                    r"返回\s+([A-Za-z0-9_* ]+)",
                                    question_text
                                )

                                if return_match:
                                    required_expression = re.sub(
                                        r"\s+",
                                        "",
                                        return_match.group(1)
                                    )

                                    returns = [
                                        node
                                        for node in ast.walk(
                                            function_node
                                        )
                                        if isinstance(
                                            node,
                                            ast.Return
                                        )
                                    ]

                                    if not returns:
                                        plan_is_safe = False
                                        print(
                                            "V6.9 PLAN_VERIFY: "
                                            "目标函数没有 return"
                                        )
                                    else:
                                        actual_expression = ast.dump(
                                            returns[-1].value,
                                            annotate_fields=False,
                                            include_attributes=False
                                        )

                                        required_expression_ast = ast.dump(
                                            ast.parse(
                                                required_expression,
                                                mode="eval"
                                            ).body,
                                            annotate_fields=False,
                                            include_attributes=False
                                        )

                                        if (
                                            actual_expression
                                            != required_expression_ast
                                        ):
                                            return_value = returns[-1].value

                                            if isinstance(
                                                return_value,
                                                ast.Name
                                            ):
                                                assigned_expression = None

                                                for node in ast.walk(
                                                    function_node
                                                ):
                                                    if isinstance(
                                                        node,
                                                        ast.Assign
                                                    ):
                                                        for target in node.targets:
                                                            if (
                                                                isinstance(
                                                                    target,
                                                                    ast.Name
                                                                )
                                                                and target.id
                                                                == return_value.id
                                                            ):
                                                                assigned_expression = node.value

                                                if assigned_expression is not None:
                                                    actual_expression = ast.dump(
                                                        assigned_expression,
                                                        annotate_fields=False,
                                                        include_attributes=False
                                                    )

                                            if (
                                                actual_expression
                                                != required_expression_ast
                                            ):
                                                plan_is_safe = False
                                                print(
                                                    "V6.9 PLAN_VERIFY: "
                                                    "返回表达式不一致"
                                                )

                        except SyntaxError as e:
                            plan_is_safe = False
                            print(
                                "V6.9 PLAN_VERIFY: "
                                "GPT 返回源码存在 Python 语法错误:",
                                e
                            )

                    # V6.4 deterministic safety gate:
                    # 如果用户需求明确给出了返回表达式，
                    # 修改计划不得篡改该表达式。
                    question_text = str(
                        self.state.get("question", "")
                    )
                    plan_text = str(
                        self.state.get("modify_plan", "")
                    )

                    return_match = re.search(
                        r"返回\s+([A-Za-z0-9_* ]+)",
                        question_text
                    )

                    if return_match:
                        required_expression = re.sub(
                            r"\s+",
                            "",
                            return_match.group(1)
                        )

                        plan_expressions = []

                        plan_expressions.extend(
                            re.findall(
                                r"return\s+([A-Za-z0-9_* ]+)",
                                plan_text,
                                re.I
                            )
                        )

                        plan_expressions.extend(
                            re.findall(
                                r"返回[ \t]+([A-Za-z0-9_* \t]+)",
                                plan_text,
                                re.I
                            )
                        )

                        if plan_expressions:
                            plan_expression = re.sub(
                                r"\s+",
                                "",
                                plan_expressions[-1]
                            )

                            if (
                                plan_expression
                                != required_expression
                            ):
                                plan_is_safe = False

                                print(
                                    "V6.4 PLAN_VERIFY 安全闸门: "
                                    "返回表达式与用户需求不一致"
                                )
                                print(
                                    "V6.4 要求:",
                                    required_expression
                                )
                                print(
                                    "V6.4 计划:",
                                    plan_expression
                                )

                    if plan_is_safe:
                        self.state["plan_verify_passed"] = True

                        # V6.9：PLAN_VERIFY 确定性安全检查通过后，
                        # 下一步由状态机直接进入 MODIFY。
                        # 不再重复调用 GPT Decision Layer。
                        self.state["phase"] = "MODIFY"
                        self.state["can_modify"] = True

                        print(
                            "V6.9 PLAN_VERIFY 通过 → 直接进入 MODIFY"
                        )

                        self.state["phase"] = "MODIFY"

                        print(
                            "V6.6 PLAN_VERIFY 通过 + GPT 已确认 → MODIFY"
                        )
                        continue

                        self.state["phase"] = "MODIFY"

                        print(
                            "V6.6 PLAN_VERIFY 通过 + GPT 已确认 → MODIFY"
                        )
                        continue

                    self.state["plan_verify_passed"] = False
                    self.state["can_modify"] = False
                    self.state["summary_done"] = True
                    self.state["phase"] = "SUMMARY"

                    print(
                        "V6.4 PLAN_VERIFY 安全闸门触发 → 禁止 MODIFY"
                    )
                    continue

                self.state["plan_verify_passed"] = False
                self.state["can_modify"] = False
                self.state["summary_done"] = True
                self.state["phase"] = "SUMMARY"

                print(
                    "V6.3 PLAN_VERIFY 未通过 → 禁止 MODIFY"
                )
                continue


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

                if (
                    self.state.get("task_mode", "") == "CHANGE"
                    and self.state.get(
                        "codex_modified_source",
                        ""
                    ).strip()
                ):
                    response = (
                        "<write_file>\n"
                        + str(self.state["target_file"])
                        + "\n"
                        + str(
                            self.state["codex_modified_source"]
                        ).strip()
                        + "\n</write_file>"
                    )

                    print(
                        "V6.9 MODIFY: 使用普通 ChatGPT 已生成的完整源码"
                    )
                else:
                    response = self.ask_llm(
                        modify_prompt
                    )

                print(response)

                tool, args = self.extract_tool(
                    response
                )

                # V6.1.11:
                # MODIFY 的目标文件必须由状态机锁定。
                # 禁止 LLM 自行决定写入路径。
                target_file = self.state.get(
                    "target_file",
                    ""
                )

                if tool == "write_file" and target_file:
                    parts = args.split("|", 1)

                    if len(parts) == 2:
                        args = (
                            target_file
                            + "|"
                            + parts[1]
                        )

                        print(
                            "V6.1.11 MODIFY 强制目标文件:",
                            target_file
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

                # V6.1.8:
                # 独立保存修改后的实际源码。
                # verify_result 后续用于保存 LLM 的验证结论，
                # 两者不能共用同一个 state 字段。
                self.state["verify_result_source"] = str(
                    verify_result
                )
                print(
                    "V6.4 VERIFY_RESULT 实际源码:",
                    repr(self.state["verify_result_source"])
                )

                # V6.9：CHANGE 任务的最终验证由 V6 确定性完成。
                # 不再重复调用本地 Qwen。
                #
                # 已完成：
                # 1. 实际重新读取修改后的源码
                # 2. 文件存在且读取成功
                # 3. 后续返回表达式安全闸门继续执行
                # 4. CHANGE 的最终结果由确定性规则决定

                actual_source_text = str(
                    self.state.get(
                        "verify_result_source",
                        ""
                    )
                )

                # V6.10：read_file_chunk 返回的源码可能带有
                # “1: ”、“2: ”这样的行号前缀。
                # VERIFY_RESULT 的 compile() 必须使用纯 Python 源码。
                actual_source_lines = []

                for source_line in actual_source_text.splitlines():
                    actual_source_lines.append(
                        re.sub(
                            r"^\s*\d+\s*:\s?",
                            "",
                            source_line
                        )
                    )

                actual_source_text = "\n".join(
                    actual_source_lines
                )

                verify_is_safe = bool(
                    actual_source_text.strip()
                )

                if verify_is_safe:
                    try:
                        compile(
                            actual_source_text,
                            modified_file,
                            "exec"
                        )
                    except Exception as e:
                        verify_is_safe = False
                        print(
                            "V6.9 VERIFY_RESULT 语法检查失败:",
                            type(e).__name__,
                            str(e)
                        )

                question_text = str(
                    self.state.get("question", "")
                )

                # CHANGE 任务至少要求实际源码已经成功读取。
                # 原有返回表达式安全闸门继续负责明确返回值要求。
                passed = verify_is_safe

                result = (
                    "VERIFY_RESULT:\n"
                    + ("PASS" if passed else "FAIL")
                    + "\n\n理由:\n"
                    + (
                        "实际修改后的源码已成功读取，"
                        "Python 语法检查通过。"
                        if passed
                        else
                        "实际修改后的源码读取或 Python 语法检查失败。"
                    )
                )

                self.state["verify_result"] = result
                self.state["verify_result_done"] = True

                # V6.4 deterministic safety gate:
                # LLM 可以发现更多问题，但不能让明显违反用户
                # 明确返回表达式的实际源码获得 PASS。
                verify_is_safe = passed

                question_text = str(
                    self.state.get("question", "")
                )
                actual_source = str(
                    self.state.get(
                        "verify_result_source",
                        ""
                    )
                )

                return_match = re.search(
                    r"返回\\s+([A-Za-z0-9_.*\\s]+)",
                    question_text
                )

                if return_match and verify_is_safe:
                    required_expression = re.sub(
                        r"\s+",
                        "",
                        return_match.group(1)
                    )

                    actual_returns = re.findall(
                        r"return\\s+([A-Za-z0-9_.*\\s]+)",
                        actual_source,
                        re.I
                    )

                    if actual_returns:
                        actual_expression = re.sub(
                            r"\s+",
                            "",
                            actual_returns[-1]
                        )

                        if (
                            actual_expression
                            != required_expression
                        ):
                            verify_is_safe = False

                            print(
                                "V6.4 VERIFY_RESULT 安全闸门: "
                                "实际返回表达式与用户需求不一致"
                            )
                            print(
                                "V6.4 要求:",
                                required_expression
                            )
                            print(
                                "V6.4 实际:",
                                actual_expression
                            )

                self.state["verify_result_passed"] = (
                    verify_is_safe
                )

                print(
                    "VERIFY_RESULT result =",
                    result
                )

                print(
                    "VERIFY_RESULT passed =",
                    passed
                )

                # V6.10：VERIFY_RESULT 只验证“本轮修改”。
                # 这里进一步判断“整个用户任务”是否已经完成。
                completion = self.check_task_completion()

                self.state["task_complete"] = completion["complete"]
                self.state["task_progress"] = completion["progress"]
                self.state["remaining_requirements"] = completion["remaining"]

                print(
                    "V6.10 TASK_COMPLETION:",
                    "complete =",
                    completion["complete"]
                )
                print(
                    "V6.10 TASK_PROGRESS:",
                    completion["progress"]
                )
                print(
                    "V6.10 TASK_REMAINING:",
                    completion["remaining"]
                )

                if completion["complete"]:
                    self.state["phase"] = "SUMMARY"
                    print(
                        "V6.10 TASK_COMPLETE → SUMMARY"
                    )
                    continue

                # V6.10：任务级完成判断与 CHANGE 分析证据必须隔离。
                # check_task_completion() 的 GPT 输出只负责判断：
                # “整个用户任务是否完成”。
                # 它绝不能成为下一轮 CHANGE VERIFY 的分析证据。
                #
                # 因此进入下一轮前，彻底清除本轮分析/修改上下文，
                # 下一轮必须重新 READ → ANALYZE。
                self.state["analysis"] = ""
                self.state["analysis_result"] = ""
                self.state["codex_analysis"] = ""
                self.state["codex_response"] = ""
                self.state["codex_modified_source"] = ""
                self.state["expected_result"] = ""
                self.state["analysis_evidence_invalid"] = False

                # 任务尚未完成：
                # 开始下一轮真实 GPT↔V6 协作。
                self.state["collaboration_round"] = (
                    self.state.get("collaboration_round", 0) + 1
                )

                print(
                    "V6.10 CONTINUE: 开始下一轮 GPT↔V6 协作，round =",
                    self.state["collaboration_round"]
                )

                # 下一轮必须重新读取真实源码。
                self.state["read_index"] = 0
                self.state["analysis_context"] = []
                self.state["read_done"] = False

                self.state["analyze_done"] = False
                self.state["verify_done"] = False
                self.state["modify_plan_done"] = False
                self.state["modify_plan"] = ""
                self.state["plan_verify_done"] = False
                self.state["plan_verify_passed"] = False
                self.state["plan_verify_result"] = ""

                self.state["verify_result_done"] = False
                self.state["verify_result_passed"] = False
                self.state["verify_result"] = ""
                self.state["verify_result_source"] = ""

                self.state["problem_confirmed"] = False
                self.state["can_modify"] = False

                # target_file 保留，SEARCH 会复用可靠目标文件。
                self.state["phase"] = "READ"

                continue


            # V6.2:
            # SUMMARY 中若分析证据与真实源码不匹配，
            # 必须在 LLM 调用之前拦截，禁止再次生成错误结论。
            if (
                self.state.get("phase") == "SUMMARY"
                and self.state.get("analysis_evidence_invalid", False)
            ):
                response = (
                    "### 最终报告\\n\\n"
                    "1. **执行结果**：未执行修改。\\n\\n"
                    "2. **原因**：分析阶段引用的代码与真实源码不一致。\\n\\n"
                    "3. **安全处理**：V6.2 安全闸门已阻止 MODIFY。\\n\\n"
                    "4. **结论**：本次任务未对目标文件执行任何修改。"
                )
                self.state["summary_done"] = True
                print("V6.2 SUMMARY: 证据失效，跳过 LLM 总结")
                print()
                print("========== 最终报告 ==========")
                print(response)
                print()
                print("========== 任务完成 ==========")
                break

            # V6.3:
            # PLAN_VERIFY 失败后由状态机直接生成确定性失败报告，
            # 禁止再次进入通用 LLM 流程。
            if (
                self.state.get("phase") == "SUMMARY"
                and self.state.get("task_mode") == "CHANGE"
                and self.state.get("plan_verify_done", False)
                and not self.state.get("plan_verify_passed", False)
                and not self.state.get("verify_result_done", False)
            ):
                response = (
                    "### 最终报告\n\n"
                    "1. **执行结果**：未执行修改。\n\n"
                    "2. **原因**：修改计划未通过 PLAN_VERIFY。\n\n"
                    "3. **安全处理**：V6.3 已阻止进入 MODIFY。\n\n"
                    "4. **结论**：本次任务未对目标文件执行修改。"
                )

                self.state["summary_done"] = True

                print(
                    "V6.3 SUMMARY: PLAN_VERIFY 未通过，"
                    "跳过 LLM 总结"
                )
                print()
                print("========== 最终报告 ==========")
                print(response)
                print()
                print("========== 任务完成 ==========")

                break


            # V6.9：
            # CHANGE 任务在 VERIFY 阶段已经确认需求满足时，
            # 不需要修改，也不需要再次调用 LLM 总结。
            if (
                self.state.get("phase") == "SUMMARY"
                and self.state.get("task_mode") == "CHANGE"
                and not self.state.get("problem_confirmed", False)
                and self.state.get("summary_done", False)
                and not self.state.get("verify_result_done", False)
            ):
                response = (
                    "### 最终报告\\n\\n"
                    "1. **执行结果**：无需修改。\\n\\n"
                    "2. **原因**：当前源码已经满足用户明确提出的修改要求。\\n\\n"
                    "3. **安全处理**：V6.9 确认无需进入 MODIFY。\\n\\n"
                    "4. **结论**：本次任务无需修改目标文件。"
                )

                print(
                    "V6.9 SUMMARY: CHANGE 需求已满足，跳过 LLM 总结"
                )
                print()
                print("========== 最终报告 ==========")
                print(response)
                print()
                print("========== 任务完成 ==========")

                break


            # V6.3:
            # CHANGE 任务在 VERIFY_RESULT 完成后，
            # 结果已经由状态机确定，不再调用 LLM 总结。
            if (
                self.state.get("phase") == "SUMMARY"
                and self.state.get("task_mode") == "CHANGE"
                and self.state.get("verify_result_done", False)
            ):
                verify_passed = self.state.get(
                    "verify_result_passed",
                    False
                )

                if verify_passed:
                    response = (
                        "### 最终报告\n\n"
                        "1. **执行结果**：修改成功。\n\n"
                        "2. **修改文件**："
                        + str(
                            self.state.get(
                                "target_file",
                                ""
                            )
                        )
                        + "\n\n"
                        "3. **修改内容**：已按照用户原始需求完成代码修改。\n\n"
                        "4. **验证结果**：VERIFY_RESULT 已通过，"
                        "修改后的实际源码满足用户原始需求。\n\n"
                        "5. **结论**：本次任务已完成。"
                    )
                else:
                    response = (
                        "### 最终报告\n\n"
                        "1. **执行结果**：修改未通过最终验证。\n\n"
                        "2. **修改文件**："
                        + str(
                            self.state.get(
                                "target_file",
                                ""
                            )
                        )
                        + "\n\n"
                        "3. **验证结果**：VERIFY_RESULT 未通过。\n\n"
                        "4. **安全处理**：未确认修改结果满足用户需求。\n\n"
                        "5. **结论**：本次任务未确认成功完成。"
                    )

                self.state["summary_done"] = True

                print(
                    "V6.3 SUMMARY: CHANGE 结果确定，"
                    "跳过 LLM 总结"
                )
                print()
                print("========== 最终报告 ==========")
                print(response)
                print()
                print("========== 任务完成 ==========")

                break


            print(
                "V6 DEBUG BEFORE LLM:",
                "phase =", self.state.get("phase"),
                "prompt_chars =", len(str(prompt))
            )

            if self.state.get("phase") == "ANALYZE":
                response = (
                    "<analyze_code>\n"
                    + str(
                        self.state.get(
                            "target_file",
                            ""
                        )
                    )
                    + "\n</analyze_code>"
                )

                print(
                    "V6.9 ANALYZE: direct analyze_code, skip LLM"
                )
            else:
                response = self.ask_llm(
                    prompt
                )

            print(response)

            # V6.6:
            # GPT Decision Protocol。
            # 如果外部提供 GPT ACTION，则先经过 V6 安全验证。
            # 当前不改变既有状态机执行逻辑。
            decision = self.decision_step(
                response
            )

            self.state["last_action"] = decision.get(
                "action",
                ""
            )

            print(
                "V6.6 Decision:",
                decision
            )

            # V6.6 ANALYZE Decision Gate:
            # GPT 决定 VERIFY 后，立即切换状态并结束本轮。
            # 防止旧版 V4.3.4 强制 analyze_code 再次接管控制流。
            if (
                self.state["phase"] == "ANALYZE"
                and decision.get("allowed", False)
            ):
                if decision.get("action") == "VERIFY":
                    self.state["phase"] = "VERIFY"
                    print(
                        "V6.6 ANALYZE: GPT 已确认进入 VERIFY"
                    )
                    continue

                print(
                    "V6.6 ANALYZE ACTION 被拒绝:",
                    decision.get("validation_reason", "")
                )
                continue


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
                    r"(/[^\s]+\.(?:py|cpp|cc|c|h|hpp))",
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

                # V6.1.11:
                # 新一轮 CHANGE 任务可能省略文件路径。
                # 如果上一轮已经确定了可靠目标文件，
                # 直接复用 target_file，禁止重新 SEARCH。
                existing_target = self.state.get(
                    "target_file",
                    ""
                )

                if (
                    existing_target
                    and os.path.isfile(existing_target)
                ):
                    self.state["target_files"] = [
                        existing_target
                    ]
                    self.state["read_index"] = 0
                    self.state["analysis_context"] = []
                    self.state["read_done"] = False

                    print(
                        "V6.1.11 reuse target file:",
                        existing_target
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

                        # V6.6: READ 结果进入下一轮 GPT Decision Request
                self.state["decision_request"] = (
                    self.build_decision_request(
                        observation
                    )
                )

                print(
                    "V6.6 READ Decision Request 已刷新:",
                    len(self.state["decision_request"]),
                    "chars"
                )

                continue

            # V4.9.2：
            # READ 阶段允许连续读取多个候选文件。
            # 不再使用旧版 read_done 阻止后续 READ。

            result = self.controller.call(
                tool,
                args
            )

            # V6.6:
            # 工具真实执行完成后建立标准 RESULT。
            # RESULT 只记录真实执行结果，不改变现有状态机。
            result_success = result is not None

            self.state["last_result"] = (
                self.build_result(
                    tool,
                    result_success,
                    output=result if result_success else "",
                    error="" if result_success else "工具执行返回空结果"
                )
            )

            print(
                "V6.6 RESULT 已生成:",
                len(self.state["last_result"]),
                "chars"
            )

            # V6.6:
            # 工具真实执行完成后建立标准 RESULT。
            # RESULT 只记录真实执行结果，不改变现有状态机。
            result_success = result is not None

            self.state["last_result"] = (
                self.build_result(
                    tool,
                    result_success,
                    output=result if result_success else "",
                    error="" if result_success else "工具执行返回空结果"
                )
            )

            print(
                "V6.6 RESULT 已生成:",
                len(self.state["last_result"]),
                "chars"
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

                        # V6.9 CHANGE：目标文件必须提供完整源码，
                        # 普通 ChatGPT 需要完整文件才能生成可直接写回的源码。
                        if (
                            self.state.get("task_mode", "") == "CHANGE"
                            and file_path == self.state.get("target_file", "")
                        ):
                            summary = source
                        else:
                            # 非 CHANGE 场景继续保持原有摘要策略。
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

                    task_mode = self.state.get(
                        "task_mode",
                        "REVIEW"
                    )

                    if task_mode == "CHANGE":

                        analysis_prompt = f"""
请根据用户明确提出的修改要求，检查下面提供的 Python 源码。

用户要求:
{self.state.get("question", "")}

源码:
{analysis_source}

当前是 CHANGE 任务。

你的唯一判断目标是：

当前真实源码是否已经满足用户明确提出的“源码功能要求”。

特别重要：

用户任务中如果出现：
“不要一次性完成全部任务”
“必须根据实际完成情况进行多轮 GPT↔V6 协作”
“逐步扩展”
“根据实际进展继续下一轮”

这些内容属于 V6 的运行编排要求，
不是目标 Python 文件本身必须实现的源码功能。

你绝对不能因为当前源码没有“GPT↔V6 协作过程”、
没有“多轮执行记录”或没有任何 Agent 编排代码，
就认定目标 Python 文件存在这个问题。

本轮 CHANGE 的目标不是一次性完成用户全部功能。

如果当前源码没有满足全部功能要求：
1. 必须指出当前真实源码尚未满足的功能；
2. 结合用户原始任务和当前真实源码，
   选择当前最合理、最小的一个功能增量进行本轮修改；
3. 只完成这个本轮增量；
4. 不要提前把所有剩余功能一次性加入；
5. 修改完成后，下一轮由 V6 重新读取真实源码，
   再根据剩余需求继续工作。

因此：
“整个任务尚未完成”不等于“本轮不能修改”。

只要当前源码存在明确未完成的功能要求，
就应该生成能够直接写回目标文件的本轮完整修改源码。

请首先输出一行：

需求满足: YES

或：

需求满足: NO

其中：
- YES = 当前真实源码已经满足用户的全部明确要求。
- NO = 当前真实源码至少有一项明确要求没有满足。

如果源码不满足用户要求，必须认定为明确问题。

特别注意：

1. 用户明确要求增加、删除、修改或实现某项功能时，
   “当前源码没有实现该要求”本身就是明确问题。
2. 不需要另外寻找与用户要求无关的 Bug。
3. 不要根据函数名称猜测需求。
4. 不要讨论用户没有要求的优化。
5. 只能使用上面提供的真实源码作为证据。
6. 文件路径、行号、代码必须来自真实源码。
7. 如果源码已经满足用户要求，才输出“未发现明确问题”。

如果源码不满足用户要求，必须输出：

Python 审查:

问题:
<明确说明源码没有满足用户要求的地方>

证据:

文件: <源码中的完整文件路径>
行号: <源码中的实际行号>
代码: <直接引用提供源码中的代码>
说明: <明确说明这段源码为什么不满足用户要求>

结论:
<明确说明当前实现不满足用户要求>

如果源码已经满足用户要求：

问题:
未发现明确问题

证据:
<引用真实源码>

结论:
未发现明确问题

禁止：
- 猜测没有提供的代码
- 编造源码
- 编造行号
- 把潜在风险当成问题
- 因为函数名称而推测需求
- 寻找与用户要求无关的 Bug

如果“需求满足: NO”，在完成问题判断后，
必须继续输出：

修改方案:
<明确说明需要修改什么>

修改后完整源码:
<修改后的完整文件内容>

预期结果:
<修改后如何满足用户明确要求>

重要要求：

1. 修改后完整源码必须是一个完整、可直接写回目标文件的 Python 文件。
2. 必须保留原文件中与本次任务无关的内容。
3. 只能修改用户明确要求的部分。
4. 不允许顺手优化其他代码。
5. 不允许修改其他文件。
6. 不允许省略代码。
7. 不允许使用“其余代码不变”等省略写法。
8. 修改后的源码必须保持 Python 语法正确。
9. 必须保持用户要求中明确指定的原有计算逻辑。
10. 如果“需求满足: YES”，不要生成修改后源码。

只根据给出的真实源码和用户明确要求执行。
使用简短中文回答。
"""

                    else:

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

问题:
未发现明确问题

结论:
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

                # V6.8：
                # 生成普通 Codex 聊天请求。
                # 不自动调用 Codex。
                # 等待普通 Codex 返回分析结果。
                codex_request = self.ask_codex(
                    analysis_prompt
                )

                self.state["codex_request"] = (
                    codex_request
                )
                self.state["waiting_codex"] = True
                self.state["phase"] = "WAIT_CODEX"

                print(
                    "V6.8 WAIT_CODEX:",
                    "request_chars =",
                    len(str(codex_request))
                )

                codex_response = self.codex.ask(
                    codex_request
                )

                self.state["codex_response"] = (
                    str(codex_response)
                )

                self.state["waiting_codex"] = False

                return self.resume_after_codex(
                    codex_response
                )

                # ANALYZE 完成后建立 GPT Decision Request。
                # 当前只保存请求，不调用 GPT，
                # 不改变现有 VERIFY → MODIFY 流程。
                self.state["decision_request"] = (
                    self.build_decision_request(
                        observation
                    )
                )

                print(
                    "V6.6 Decision Request 已生成:",
                    len(
                        self.state["decision_request"]
                    ),
                    "chars"
                )

                # V6.6：第一次真正独立调用 Decision Layer。
                decision_response = self.ask_decision(
                    self.state["decision_request"]
                )

                decision = self.decision_step(
                    decision_response
                )

                print(
                    "V6.6 GPT Decision:",
                    decision
                )

                # V6.6：ACTION 正式成为状态推进门控。
                # ANALYZE 阶段只能接受 VERIFY。
                if not decision.get("allowed", False):
                    print(
                        "V6.6 ACTION 被拒绝:",
                        decision.get("validation_reason", "")
                    )
                    self.state["summary_done"] = False
                    self.state["phase"] = "ANALYZE"
                    continue

                if decision.get("action") != "VERIFY":
                    print(
                        "V6.6 ACTION 与当前阶段预期不一致:",
                        decision.get("action"),
                        "!= VERIFY"
                    )
                    self.state["summary_done"] = False
                    self.state["phase"] = "ANALYZE"
                    continue

                print("========== VERIFY 阶段 ==========")

                # V6.6：
                # GPT 已明确决定 VERIFY，
                # V6 安全验证通过后才允许状态机推进。
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

                        continue


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

            # V6.6:
            # 每次工具执行完成后，刷新下一轮 GPT Decision Request。
            # 不改变现有 observation，也不改变状态机流程。
            self.state["decision_request"] = (
                self.build_decision_request(
                    observation
                )
            )

            print(
                "V6.6 Decision Request 已刷新:",
                len(self.state["decision_request"]),
                "chars"
            )
