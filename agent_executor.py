import os
import re
import json


class AgentExecutor:

    def __init__(self, llm, router):
        self.llm = llm
        self.router = router

    def _search(self, keyword):
        try:
            return self.router.call(
                "search_code_index",
                keyword
            )
        except Exception as e:
            print(f"搜索失败 [{keyword}]: {e}")
            return ""

    def _extract_search_terms(self, question):
        terms = []

        identifiers = re.findall(
            r"[A-Za-z_][A-Za-z0-9_.-]{2,}",
            question
        )

        for item in identifiers:
            if item not in terms:
                terms.append(item)

        for item in list(terms):
            if "_" in item:
                alt = item.replace("_", "-")
                if alt not in terms:
                    terms.append(alt)

        stop_words = {
            "the",
            "this",
            "that",
            "code",
            "source",
            "analyze",
            "analysis",
            "implementation",
        }

        return [
            x for x in terms
            if x.lower() not in stop_words
        ]

    def _extract_files(self, search_result):
        files = []

        if not search_result:
            return files

        try:
            data = json.loads(search_result)

            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        path = item.get("file")

                        if (
                            isinstance(path, str)
                            and os.path.isfile(path)
                        ):
                            files.append(path)

        except Exception:
            pass

        if not files:
            matches = re.findall(
                r'"file"\s*:\s*"([^"]+)"',
                search_result
            )

            for path in matches:
                if os.path.isfile(path):
                    files.append(path)

        result = []

        for path in files:
            if path not in result:
                result.append(path)

        return result

    def _rank_files(self, files, terms):

        def score(path):
            name = os.path.basename(path).lower()
            stem = os.path.splitext(name)[0]

            value = 0

            for term in terms:
                t = term.lower()

                if t == stem:
                    value += 100

                if t in name:
                    value += 50

                if t.replace("_", "-") in name:
                    value += 40

            if name.endswith(".cpp"):
                value += 20

            if name.endswith(".h"):
                value += 10

            return value

        return sorted(
            files,
            key=score,
            reverse=True
        )

    def _read_file(self, path):
        try:
            with open(
                path,
                "r",
                encoding="utf-8"
            ) as f:
                return f.read()

        except Exception as e:
            return f"[读取失败: {path}: {e}]"

    def _extract_functions(self, code):
        """
        从 server-queue.cpp 中提取 server_queue 相关函数。
        """

        patterns = [
            r"server_queue::post",
            r"server_queue::defer",
            r"server_queue::get_new_id",
            r"server_queue::pop_deferred_task",
            r"server_queue::wait_until_no_sleep",
            r"server_queue::terminate",
            r"server_queue::start_loop",
        ]

        lines = code.splitlines()

        blocks = []

        current = []
        capturing = False
        brace_count = 0

        for line in lines:

            if any(
                pattern in line
                for pattern in patterns
            ):
                if current:
                    blocks.append("\n".join(current))

                current = [line]
                capturing = True
                brace_count = line.count("{") - line.count("}")

                if brace_count <= 0:
                    continue

            elif capturing:
                current.append(line)

                brace_count += (
                    line.count("{")
                    - line.count("}")
                )

                if brace_count <= 0:
                    blocks.append(
                        "\n".join(current)
                    )

                    current = []
                    capturing = False

        if current:
            blocks.append("\n".join(current))

        return blocks

    def _analyze_block(self, question, block, index, total):

        print(
            f"\n========== 分析核心函数 {index}/{total} =========="
        )

        print(
            block.splitlines()[0]
            if block.strip()
            else "空代码块"
        )

        prompt = f"""
你是资深 C++ 工程师。

用户问题：
{question}

下面是 llama.cpp 中一个真实函数：

{block}

请严格根据这段真实源码分析。

只回答：

1. 函数作用
2. 输入/参数
3. 核心执行流程
4. 涉及的队列、锁、条件变量或线程机制
5. 对其他函数的影响

限制：
- 不要重复源码
- 不要猜测
- 不要扩展到没有提供的代码
- 总长度控制在 250 字以内
"""

        try:
            result = self.llm.invoke(prompt)

            text = getattr(
                result,
                "content",
                str(result)
            )

            print("\n分析结果:")
            print(text)

            return text

        except Exception as e:

            print(
                f"\n模型分析失败: {e}"
            )

            return ""

    def run(self, question):

        print("\n任务:", question)

        terms = self._extract_search_terms(
            question
        )

        if not terms:
            terms = [question.strip()]

        print("\n搜索关键词:")
        print(", ".join(terms))

        all_files = []

        for keyword in terms:

            print(
                f"\n搜索: {keyword}"
            )

            result = self._search(
                keyword
            )

            files = self._extract_files(
                result
            )

            for path in files:

                if path not in all_files:
                    all_files.append(path)

        if not all_files:

            print("\n没有找到源码文件")
            return

        ranked = self._rank_files(
            all_files,
            terms
        )

        print("\n找到源码文件:")

        for path in ranked[:10]:
            print(" -", path)

        cpp_files = [
            p for p in ranked
            if p.endswith(".cpp")
        ]

        if not cpp_files:

            print("\n没有找到 C++ 源文件")
            return

        main_file = cpp_files[0]

        print(
            "\n主分析文件:",
            main_file
        )

        code = self._read_file(
            main_file
        )

        functions = self._extract_functions(
            code
        )

        if not functions:

            print(
                "\n没有提取到 server_queue 核心函数"
            )
            return

        print(
            f"\n提取到 {len(functions)} 个核心函数"
        )

        results = []

        total = len(functions)

        for i, block in enumerate(
            functions,
            1
        ):

            result = self._analyze_block(
                question,
                block,
                i,
                total
            )

            if result:
                results.append(result)

        if not results:

            print("\n没有获得分析结果")
            return

        print(
            "\n\n================================"
        )
        print(
            "核心函数分析完成，开始总结"
        )
        print(
            "================================"
        )

        summary_source = "\n\n".join(
            results
        )

        summary_prompt = f"""
你是资深 C++ 架构工程师。

用户问题：
{question}

下面是已经根据真实源码得到的函数分析：

{summary_source}

请做一个简短总结。

输出：

1. server_queue 的整体职责
2. 任务进入队列的方式
3. 延迟任务如何处理
4. 睡眠/唤醒机制
5. 线程安全机制
6. terminate 如何结束队列
7. 修改这个模块时最大的风险

严格依据上面的分析。
不要虚构源码。

控制在 500 字以内。
"""

        try:

            result = self.llm.invoke(
                summary_prompt
            )

            text = getattr(
                result,
                "content",
                str(result)
            )

            print(
                "\n=============================="
            )
            print(
                "最终分析结果:"
            )
            print(
                "=============================="
            )

            print(text)

        except Exception as e:

            print(
                f"\n总结失败: {e}"
            )
