import os
import subprocess


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


    def ask(self, prompt):

        result = subprocess.run(

            [
                self.codex,
                "exec",

                "-m",
                self.model,

                "--sandbox",
                "read-only",

                prompt
            ],

            env=self._environment(),

            capture_output=True,

            text=True,

            timeout=self.timeout
        )


        if result.returncode != 0:

            raise RuntimeError(
                "Codex failed: "
                + result.stderr
            )


        return result.stdout.strip()
