import re



class Planner:


    def __init__(
        self
    ):


        self.keywords = [

            "分析",

            "源码",

            "实现",

            "架构",

            "线程",

            "队列",

            "server_queue",

            "queue"

        ]



    def detect(
        self,
        question
    ):


        text = question.lower()


        for item in self.keywords:


            if item.lower() in text:

                return "analysis"



        return "normal"



    def extract_keyword(
        self,
        question
    ):


        patterns = [

            r"的\s*([a-zA-Z_][a-zA-Z0-9_]*)",

            r"分析\s+([a-zA-Z_][a-zA-Z0-9_]*)",

            r"实现\s+([a-zA-Z_][a-zA-Z0-9_]*)",

        ]



        for pattern in patterns:


            result = re.search(
                pattern,
                question
            )


            if result:

                return result.group(
                    1
                )



        words = re.findall(
            r"[a-zA-Z_][a-zA-Z0-9_]*",
            question
        )


        if words:

            return words[-1]



        return question
