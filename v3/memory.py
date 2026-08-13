import os
import json
import time


class Memory:


    def __init__(
        self
    ):

        self.path = os.path.expanduser(
            "~/.ai_programmer_v3_memory.json"
        )


        self.data = {

            "tools": [],

            "errors": [],

            "analysis_history": []

        }


        self.load()



    def load(
        self
    ):

        try:

            if os.path.exists(
                self.path
            ):

                with open(
                    self.path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    self.data = json.load(
                        f
                    )

        except Exception:

            pass



    def save(
        self
    ):

        try:

            with open(
                self.path,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    self.data,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

        except Exception:

            pass



    def add_tool(
        self,
        name,
        args
    ):

        self.data["tools"].append(

            {

                "time": time.time(),

                "name": name,

                "args": str(args)

            }

        )


        self.save()



    def add_error(
        self,
        error
    ):

        self.data["errors"].append(

            {

                "time": time.time(),

                "error": str(error)

            }

        )


        self.save()


    def add_analysis(
        self,
        target,
        result
    ):

        self.data["analysis_history"].append(

            {

                "time": time.time(),

                "target": target,

                "result": result

            }

        )


        self.save()


    def repeated_tool(
        self,
        name
    ):

        tools = self.data["tools"]


        if len(tools) < 3:

            return False


        return (

            tools[-1]["name"] == name

            and

            tools[-2]["name"] == name

            and

            tools[-3]["name"] == name

        )
