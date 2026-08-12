import os
import re


class AgentControllerV25:

    def __init__(self):
        self.phase = "search"
        self.last_search = False
        self.files = []


    def keyword(self, question):

        keys = [
            "server_queue",
            "server-queue",
            "llama_context",
            "scheduler",
            "queue",
            "mutex",
            "thread"
        ]

        for k in keys:
            if k.lower() in question.lower():
                return k

        result = re.findall(
            r"[A-Za-z_][A-Za-z0-9_]+",
            question
        )

        if result:
            return result[-1]

        return question


    def allow(self, tool, path=""):

        if tool == "search_code_index":

            self.last_search = True
            self.phase = "read"

            return True


        if tool in (
            "read_file",
            "read_file_chunk",
            "analyze_code"
        ):

            if not self.last_search:
                return False


            if path:

                path=os.path.expanduser(path)

                if os.path.isdir(path):
                    return False


        return True
