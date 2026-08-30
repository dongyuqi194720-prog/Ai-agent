import os
import subprocess


CODEX = os.path.expanduser(
    "~/.vscode/extensions/openai.chatgpt-26.810.52044-linux-arm64/bin/linux-aarch64/codex"
)


env = os.environ.copy()

env["HTTP_PROXY"] = "http://127.0.0.1:20171"
env["HTTPS_PROXY"] = "http://127.0.0.1:20171"
env["http_proxy"] = "http://127.0.0.1:20171"
env["https_proxy"] = "http://127.0.0.1:20171"

env["ALL_PROXY"] = ""
env["all_proxy"] = ""

env["NO_PROXY"] = "localhost,127.0.0.0/8,::1"
env["no_proxy"] = "localhost,127.0.0.0/8,::1"


prompt = "只回答：PYTHON SUBPROCESS CODEX SUCCESS"


print("===== PYTHON CODEX BRIDGE =====")
print("CODEX:", CODEX)
print("MODEL: gpt-5.6-terra")
print()


result = subprocess.run(
    [
        CODEX,
        "exec",
        "-m",
        "gpt-5.6-terra",
        "--sandbox",
        "read-only",
        prompt,
    ],
    env=env,
    capture_output=True,
    text=True,
    timeout=120,
)


print("===== RETURN CODE =====")
print(result.returncode)

print()
print("===== STDOUT =====")
print(result.stdout)

print()
print("===== STDERR =====")
print(result.stderr)
