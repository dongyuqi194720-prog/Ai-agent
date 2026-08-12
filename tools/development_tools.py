
import os

import subprocess

from langchain_core.tools import tool

def _run(cmd, cwd=None, timeout=300):

    try:

        result = subprocess.run(

            cmd,

            shell=True,

            cwd=cwd,

            capture_output=True,

            text=True,

            timeout=timeout

        )

        return (

            "returncode: "

            + str(result.returncode)

            + "\n\nstdout:\n"

            + result.stdout

            + "\n\nstderr:\n"

            + result.stderr

        )

    except Exception as e:

        return "执行失败: " + str(e)

@tool

def git_status(path: str):

    """

    查看项目 Git 状态

    """

    path = os.path.expanduser(path)

    if not os.path.isdir(path):

        return "目录不存在: " + path

    return _run(

        "git status --short --branch",

        cwd=path

    )

@tool

def git_diff(path: str):

    """

    查看项目 Git diff

    """

    path = os.path.expanduser(path)

    if not os.path.isdir(path):

        return "目录不存在: " + path

    return _run(

        "git diff --",

        cwd=path

    )

@tool

def git_log(path: str):

    """

    查看最近 Git 提交

    """

    path = os.path.expanduser(path)

    if not os.path.isdir(path):

        return "目录不存在: " + path

    return _run(

        "git log -8 --oneline",

        cwd=path

    )

@tool

def build_project(path: str):

    """

    自动尝试编译 CMake 项目

    """

    path = os.path.expanduser(path)

    if not os.path.isdir(path):

        return "项目目录不存在: " + path

    build = os.path.join(

        path,

        "build"

    )

    if not os.path.isdir(build):

        configure = _run(

            "cmake -S . -B build",

            cwd=path,

            timeout=600

        )

        if "returncode: 0" not in configure:

            return (

                "CMake配置失败\n"

                + configure

            )

    return _run(

        "cmake --build build -j2",

        cwd=path,

        timeout=1800

    )

@tool

def test_project(path: str):

    """

    自动运行 CTest

    """

    path = os.path.expanduser(path)

    build = os.path.join(

        path,

        "build"

    )

    if not os.path.isdir(build):

        return "build目录不存在，请先编译项目"

    return _run(

        "ctest --output-on-failure",

        cwd=build,

        timeout=1800

    )

