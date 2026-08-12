import os
import json
import time

from langchain_core.tools import tool


def build_code_index(project_path):

    project_path=os.path.expanduser(project_path)

    index_file=os.path.join(
        project_path,
        "llama.cpp_index.json"
    )

    index={}

    index["project"]=project_path
    index["created"]=time.time()
    index["files"]=[]


    for root,dirs,files in os.walk(project_path):

        for f in files:

            if f.endswith(
                (
                    ".cpp",
                    ".h",
                    ".hpp",
                    ".py",
                    ".cmake",
                    ".txt"
                )
            ):

                full=os.path.join(
                    root,
                    f
                )

                try:

                    size=os.path.getsize(full)

                    with open(
                        full,
                        "r",
                        encoding="utf-8",
                        errors="ignore"
                    ) as file:

                        lines=file.readlines()


                    item={
                        "file":full,
                        "size":size,
                        "lines":len(lines)
                    }


                    structs=[]
                    classes=[]
                    functions=[]


                    for line in lines[:500]:

                        line=line.strip()


                        if line.startswith("struct "):
                            structs.append(line)


                        if line.startswith("class "):
                            classes.append(line)


                        if "(" in line and ")" in line and "{" in line:
                            functions.append(line[:120])


                    item["structs"]=structs[:20]
                    item["classes"]=classes[:20]
                    item["functions"]=functions[:20]


                    index["files"].append(item)


                except Exception:
                    pass


    with open(
        index_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            index,
            f,
            ensure_ascii=False,
            indent=2
        )


    return index_file



@tool
def create_project_index(path:str):
    """
    创建代码项目索引
    """

    return build_code_index(path)



@tool
def search_code_index(keyword:str):
    """
    搜索项目代码索引
    """

    try:

        index_file=os.path.expanduser(
            "~/llama.cpp/llama.cpp_index.json"
        )


        if not os.path.exists(index_file):
            return "索引不存在，请先创建索引"


        with open(
            index_file,
            "r",
            encoding="utf-8"
        ) as f:

            index=json.load(f)


        result=[]

        keyword=keyword.lower()


        for item in index.get("files", []):

            text=json.dumps(
                item,
                ensure_ascii=False
            ).lower()


            if keyword in text:

                result.append(item)


        if not result:

            return "没有找到相关代码"


        return json.dumps(
            result[:30],
            ensure_ascii=False,
            indent=2
        )


    except Exception as e:

        return f"搜索索引失败:{e}"



def split_code(content, size=4000):
    """
    将代码切成小块
    """

    chunks=[]

    lines=content.splitlines()

    current=[]

    length=0


    for line in lines:

        current.append(line)

        length += len(line)


        if length >= size:

            chunks.append(
                "\n".join(current)
            )

            current=[]
            length=0


    if current:

        chunks.append(
            "\n".join(current)
        )


    return chunks


@tool
def analyze_code(path: str):
    """
    分析代码文件或目录
    """

    try:

        path=os.path.expanduser(path)

        result=""


        if os.path.isfile(path):

            with open(
                path,
                "r",
                encoding="utf-8"
            ) as f:

                content=f.read()


            result += (
                "\n\n=====文件=====\n"
                + path
                + "\n\n"
                + content[:12000]
            )


            return result



        if os.path.isdir(path):

            files=[]


            for root,dirs,fs in os.walk(path):

                for f in fs:

                    if f.endswith(
                        (
                            ".cpp",
                            ".h",
                            ".hpp",
                            ".py"
                        )
                    ):

                        files.append(
                            os.path.join(root,f)
                        )



            for file in files[:10]:

                try:

                    with open(
                        file,
                        "r",
                        encoding="utf-8"
                    ) as f:

                        content=f.read()


                    chunks=split_code(content)


                    result += (
                        "\n文件:"
                        + file
                        + "\n"
                    )


                    result += (
                        "\n代码分块数量:"
                        + str(len(chunks))
                        + "\n"
                    )


                    for i,chunk in enumerate(chunks[:3]):

                        result += (
                            "\n=====代码块 "
                            + str(i+1)
                            + " =====\n"
                            + chunk
                        )


                except:
                    pass


            return result



        return "路径不存在:"+path


    except Exception as e:

        return f"分析失败:{e}"

