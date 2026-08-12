import os

from langchain_core.tools import tool


@tool
def list_files(path: str):
    """
    查看目录结构
    """
    try:
        path=os.path.expanduser(path)

        result=[]

        for root, dirs, files in os.walk(path):

            level=root.replace(path,"").count(os.sep)

            if level > 2:
                continue

            indent="  " * level

            result.append(
                indent + os.path.basename(root) + "/"
            )

            for f in files[:20]:
                result.append(
                    indent + "  " + f
                )

        return "\n".join(result)

    except Exception as e:
        return f"目录读取失败: {e}"


@tool
def read_file(path: str):
    """
    读取文件内容
    """
    try:
        path=os.path.expanduser(path)

        # 自动补全 llama.cpp 项目路径
        if not os.path.exists(path):

            project_path="/home/baixin/llama.cpp/"+path

            if os.path.exists(project_path):
                path=project_path


        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    except Exception as e:
        return f"读取失败: {e}"



@tool
def read_file_chunk(path: str, start: int = 1, end: int = 200):
    """
    分块读取代码文件
    """
    try:
        path=os.path.expanduser(path)

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            lines=f.readlines()

        chunk=lines[start-1:end]

        result=""

        for i,line in enumerate(
            chunk,
            start=start
        ):
            result += f"{i}: {line}"

        return result

    except Exception as e:
        return f"读取失败: {e}"
