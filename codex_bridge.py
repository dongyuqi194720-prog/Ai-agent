import os
import time

from playwright.sync_api import sync_playwright


class CodexBridge:

    def __init__(
        self,
        model="gpt-5.6-terra",
        timeout=600
    ):

        self.codex = os.path.expanduser(
            "~/.vscode/extensions/"
            "openai.chatgpt-26.810.52044-linux-arm64/"
            "bin/linux-aarch64/codex"
        )

        self.model = model
        self.timeout = timeout


    def _environment(self):

        env = os.environ.copy()

        env["HTTP_PROXY"] = "http://127.0.0.1:20171"
        env["HTTPS_PROXY"] = "http://127.0.0.1:20171"

        env["http_proxy"] = "http://127.0.0.1:20171"
        env["https_proxy"] = "http://127.0.0.1:20171"

        env["ALL_PROXY"] = ""
        env["all_proxy"] = ""

        env["NO_PROXY"] = (
            "localhost,127.0.0.0/8,::1"
        )

        env["no_proxy"] = (
            "localhost,127.0.0.0/8,::1"
        )

        return env



    def build_request(
        self,
        task,
        source=None,
        state=None,
        target_files=None
    ):
        """V6 -> 普通 Codex 聊天的信息桥请求。"""

        return {
            "type": "TASK_ANALYSIS_REQUEST",
            "task": str(task),
            "target_files": target_files or [],
            "source": source or [],
            "current_state": state or {},
            "request": (
                "请完成复杂代码分析，定位问题，"
                "给出明确、可执行的修改方案。"
                "不要直接执行代码或修改文件。"
            )
        }


    def parse_response(self, response):
        """普通 Codex 聊天 -> V6 的信息桥响应。"""

        if isinstance(response, dict):
            data = response
        else:
            text = str(response).strip()

            def section_after(text, title, next_titles):
                marker = title + ":"
                pos = text.find(marker)

                if pos < 0:
                    return ""

                start_pos = pos + len(marker)
                end_pos = len(text)

                for next_title in next_titles:
                    next_pos = text.find(
                        next_title + ":",
                        start_pos
                    )

                    if next_pos >= 0:
                        end_pos = min(
                            end_pos,
                            next_pos
                        )

                return text[
                    start_pos:end_pos
                ].strip()

            plan = section_after(
                text,
                "修改方案",
                [
                    "修改后完整源码",
                    "预期结果"
                ]
            )

            modified_source = section_after(
                text,
                "修改后完整源码",
                [
                    "预期结果"
                ]
            )

            # V6.9：普通 ChatGPT 可能用 Markdown 代码围栏包裹源码。
            # Bridge 只负责提取代码围栏内部源码，不修改源码内容。
            modified_source = modified_source.strip()

            if "```" in modified_source:
                fence_parts = modified_source.split("```")

                if len(fence_parts) >= 3:
                    modified_source = fence_parts[1].strip()

                    # 去掉 ```python / ```py 等语言标记。
                    first_line = modified_source.splitlines()

                    if (
                        first_line
                        and first_line[0].strip().lower()
                        in {"python", "py"}
                    ):
                        modified_source = "\n".join(
                            first_line[1:]
                        ).strip()

            expected_result = section_after(
                text,
                "预期结果",
                []
            )

            analysis = text

            plan_pos = text.find("修改方案:")
            if plan_pos >= 0:
                analysis = text[:plan_pos].strip()

            data = {
                "analysis": analysis,
                "plan": plan,
                "modified_source": modified_source,
                "expected_result": expected_result
            }

        return {
            "type": "TASK_ANALYSIS_RESPONSE",
            "analysis": str(
                data.get("analysis", "")
            ).strip(),
            "plan": str(
                data.get("plan", "")
            ).strip(),
            "modified_source": str(
                data.get("modified_source", "")
            ).strip(),
            "target_file": str(
                data.get("target_file", "")
            ).strip(),
            "expected_result": str(
                data.get("expected_result", "")
            ).strip()
        }

    def ask(self, prompt):
        """V6 -> 普通 ChatGPT -> V6。"""

        with sync_playwright() as p:
            proxy_keys = [
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy"
            ]

            saved_proxy = {
                key: os.environ.get(key)
                for key in proxy_keys
            }

            for key in proxy_keys:
                os.environ.pop(key, None)

            try:
                import json
                import urllib.request

                req = urllib.request.Request(
                    "http://127.0.0.1:9222/json/version"
                )

                with urllib.request.urlopen(
                    req,
                    timeout=5
                ) as r:
                    cdp_info = json.load(r)

                ws_url = cdp_info.get(
                    "webSocketDebuggerUrl",
                    ""
                )

                if not ws_url:
                    raise RuntimeError(
                        "CDP webSocketDebuggerUrl 不存在"
                    )

                browser = p.chromium.connect_over_cdp(
                    ws_url
                )
            finally:
                for key, value in saved_proxy.items():
                    if value is not None:
                        os.environ[key] = value

            page = browser.contexts[0].pages[0]

            messages = page.locator(
                "[data-message-author-role='assistant']"
            )

            box = page.locator(
                "[contenteditable='true'][aria-label='与 ChatGPT 聊天']"
            ).first

            box.wait_for(state="visible", timeout=10000)

            before_messages = page.locator(
                "[data-message-author-role='assistant']"
            ).count()

            box.focus()
            box.press("Control+A")
            page.keyboard.insert_text(str(prompt))
            box.press("Enter")

            deadline = time.time() + self.timeout

            last_response = ""
            stable_count = 0

            while time.time() < deadline:
                time.sleep(1)

                all_messages = page.locator(
                    "[data-message-author-role='assistant']"
                )

                total_count = all_messages.count()

                if total_count == 0:
                    continue

                # V6.10：必须等待本次请求产生新的 assistant 消息。
                if total_count <= before_messages:
                    continue

                # 只读取本次请求产生的最后一条 assistant 消息。
                last_message = all_messages.nth(
                    total_count - 1
                )

                try:
                    response = last_message.inner_text(
                        timeout=1000
                    ).strip()
                except Exception:
                    continue

                if not response:
                    continue

                # V6.9：只要消息序列产生了新的 assistant 消息，
                # 即使文本与上一条完全相同，也认为是新回答。
                if response == last_response:
                    stable_count += 1
                else:
                    last_response = response
                    stable_count = 0

                # 连续两秒文本没有变化，认为回答已经完成。
                if stable_count >= 2:
                    browser.close()
                    return response

            browser.close()

        raise TimeoutError(
            "普通 ChatGPT 回复超时"
        )
