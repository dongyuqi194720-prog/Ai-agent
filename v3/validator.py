import os


class Validator:


    def check_file(
        self,
        path
    ):

        path = os.path.expanduser(
            path
        )


        if not os.path.exists(
            path
        ):

            return (
                False,
                "路径不存在: " + path
            )


        if os.path.isdir(
            path
        ):

            return (
                False,
                "这是目录，不是文件: " + path
            )


        return (
            True,
            path
        )
