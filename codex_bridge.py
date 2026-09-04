import os
import time

from playwright.sync_api import sync_playwright


class CodexBridge:

    def __init__(
        self,
        model="gpt-5.6-terra",
        timeout=120
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
            data = {
                "analysis": str(response).strip()
            }

        return {
            "type": "TASK_ANALYSIS_RESPONSE",
            "analysis": str(
                data.get("analysis", "")
            ).strip(),
            "plan": str(
                data.get("plan", "")
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
            browser = p.chromium.connect_over_cdp(
                "http://127.0.0.1:9222"
            )

            page = browser.contexts[0].pages[0]

            messages = page.locator(
                "[data-message-author-role='assistant']"
            )

            before_count = messages.count()

            box = page.locator(
                "[contenteditable='true'][aria-label='与 ChatGPT 聊天']"
            ).first

            box.click()
            box.fill(str(prompt))
            box.press("Enter")

            deadline = time.time() + self.timeout

            while time.time() < deadline:
                time.sleep(1)

                stop = page.locator(
                    "button[aria-label='停止回答']"
                )

                if stop.count() > 0:
                    continue

                current_count = messages.count()

                if current_count <= before_count:
                    continue

                response = messages.nth(
                    current_count - 1
                ).inner_text().strip()

                if response:
                    browser.close()
                    return response

            browser.close()

        raise TimeoutError(
            "普通 ChatGPT 回复超时"
        )
