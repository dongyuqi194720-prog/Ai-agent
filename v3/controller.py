import os


class Controller:


    def __init__(
        self,
        router,
        memory,
        validator
    ):

        self.router = router

        self.memory = memory

        self.validator = validator

        self.current_file = None



    def normalize_args(
        self,
        name,
        args
    ):


        if not isinstance(
            args,
            str
        ):

            return args



        if name == "read_file":


            if "|||" in args:

                args = args.split(
                    "|||"
                )[0]



            if (
                args.endswith(
                    "llama.cpp"
                )
                and self.current_file
            ):

                args = self.current_file



        if name == "read_file_chunk":


            parts = args.split(
                "|||"
            )


            if len(parts) == 1:

                return args + "|||1|||200"



        return args



    def check_path(
        self,
        name,
        args
    ):


        if name not in [
            "read_file",
            "read_file_chunk"
        ]:

            return True, args



        if isinstance(args, dict):

            path = args.get(
                "path",
                ""
            )

        else:

            path = args


        if "|" in path:

            path = path.split(
                "|"
            )[0]


        if "|||" in path:

            path = path.split(
                "|||"
            )[0]


        if os.path.isdir(
            path
        ):

            return False, (
                "这是目录，不是文件，请先使用 search_code_index 找真实源码文件: "
                + path
            )



        return True, args



    def save_search_result(
        self,
        result
    ):


        try:

            if isinstance(
                result,
                list
            ):

                for item in result:

                    if isinstance(
                        item,
                        dict
                    ):

                        file = item.get(
                            "file"
                        )


                        if file:

                            self.current_file = file

                            return


        except Exception:

            pass



    def call(
        self,
        name,
        args
    ):

        if name == "read_file_chunk":

            if isinstance(args, str):

                parts = args.split("|")

                if len(parts) == 3:

                    args = {
                        "path": parts[0],
                        "start": int(parts[1]),
                        "end": int(parts[2])
                    }


        # V6.1:
        # write_file 的 extract_tool 输出格式：
        # path|完整文件内容
        #
        # 只按第一个 | 分割，保证文件正文中的 | 不会被截断。
        if name == "write_file":

            if isinstance(args, str):

                parts = args.split("|", 1)

                if len(parts) == 2:

                    args = {
                        "path": parts[0],
                        "content": parts[1]
                    }


        args = self.normalize_args(
            name,
            args
        )


        ok, checked = self.check_path(
            name,
            args
        )


        if not ok:

            return checked


        try:

            result = self.router.call(
                name,
                args
            )


            if name == "search_code_index":

                self.save_search_result(
                    result
                )


            return result


        except Exception as e:

            return (
                "工具执行失败: "
                + str(e)
            )
