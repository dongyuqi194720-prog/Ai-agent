from langchain_core.tools import tool
import os
import subprocess


@tool
def run_command(cmd: str):
    """
    执行Linux终端命令
    """
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120
        )

        return (
            "stdout:\n"
            + result.stdout
            + "\n\nstderr:\n"
            + result.stderr
        )

    except Exception as e:
        return f"执行失败: {e}"


@tool
def write_file(path: str, content: str):
    """
    写入文件
    """
    try:
        path=os.path.expanduser(path)

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(content)

        return f"文件写入成功: {path}"

    except Exception as e:
        return f"写入失败: {e}"


@tool
def vscode_open(path: str):
    """
    使用VS Code打开文件
    """
    try:
        path=os.path.expanduser(path)

        subprocess.Popen(
            [
                "code",
                path
            ]
        )

        return f"已打开: {path}"

    except Exception as e:
        return f"打开失败: {e}"
