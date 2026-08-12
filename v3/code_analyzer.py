import json



class CodeAnalyzer:


    def __init__(
        self,
        router
    ):

        self.router = router



    def search(
        self,
        keyword
    ):


        result = self.router.call(
            "search_code_index",
            keyword
        )


        files = []


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


                        path = item.get(
                            "file"
                        )


                        if path:

                            files.append(
                                path
                            )



        except Exception:

            pass



        return files



    def choose(
        self,
        files
    ):


        if not files:

            return None



        score = {}



        for file in files:


            value = 0


            path = file.lower()



            if "/tests/" in path:

                value -= 100



            if "server" in path:

                value += 100



            if "queue" in path:

                value += 100



            if "scheduler" in path:

                value += 80



            if "worker" in path:

                value += 50



            if path.endswith(
                ".cpp"
            ):

                value += 10



            score[file] = value



        return max(
            score,
            key=score.get
        )



    def analyze(
        self,
        keyword
    ):


        files = self.search(
            keyword
        )


        target = self.choose(
            files
        )


        if not target:


            return {

                "error":
                "没有找到目标源码"

            }



        result = self.router.call(
            "read_file_chunk",
            target + "|||1|||300"
        )



        return {


            "file":
            target,


            "code":
            result

        }
