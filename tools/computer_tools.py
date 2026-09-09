from langchain_core.tools import tool
import os
import subprocess
from tools.gui_observer import observe_window
from tools.ocr_observer import observe_text


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


@tool
def mouse_move(x: int, y: int):
    """移动系统鼠标到指定屏幕坐标。"""
    try:
        subprocess.run(
            ["xdotool", "mousemove", str(x), str(y)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        return f"鼠标已移动到: ({x}, {y})"
    except Exception as e:
        return f"鼠标移动失败: {e}"


@tool
def mouse_click(button: int = 1, clicks: int = 1):
    """执行系统鼠标点击。button 1=左键，2=中键，3=右键。"""
    try:
        for _ in range(max(1, clicks)):
            subprocess.run(
                ["xdotool", "click", str(button)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10
            )
        return f"鼠标点击完成: button={button}, clicks={clicks}"
    except Exception as e:
        return f"鼠标点击失败: {e}"


@tool
def keyboard_type(text: str):
    """向当前活动窗口输入文本。"""
    try:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--", text],
            check=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        return "键盘输入完成"
    except Exception as e:
        return f"键盘输入失败: {e}"


@tool
def keyboard_press(key: str):
    """向当前活动窗口发送一个键盘按键，例如 Return、Tab、Escape、ctrl+c。"""
    try:
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", key],
            check=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        return f"按键完成: {key}"
    except Exception as e:
        return f"按键失败: {e}"


@tool
def window_list(request: str = ""):
    """列出当前桌面窗口。"""
    try:
        result = subprocess.run(
            ["wmctrl", "-l"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip() or "当前没有检测到窗口"
    except Exception as e:
        return f"窗口列表获取失败: {e}"


@tool
def window_activate(window_id: str):
    """激活指定窗口，window_id 使用 window_list 返回的窗口 ID。"""
    try:
        subprocess.run(
            ["wmctrl", "-i", "-a", window_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        return f"窗口已激活: {window_id}"
    except Exception as e:
        return f"窗口激活失败: {e}"


@tool
def observe_window_tool(query: str):
    """观察指定桌面窗口，返回窗口信息、截图路径和 OCR 文本。"""
    try:
        window = observe_window(query)
        if not window:
            return f"未找到窗口: {query}"

        text = observe_text(window["screenshot"])
        return (
            f"窗口: {window['title']}\n"
            f"window_id: {window['window_id']}\n"
            f"位置: ({window['x']}, {window['y']})\n"
            f"大小: {window['width']}x{window['height']}\n"
            f"截图: {window['screenshot']}\n"
            f"OCR文本:\n{text}"
        )
    except Exception as e:
        return f"窗口观察失败: {e}"
