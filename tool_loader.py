import os
import importlib.util


def load_tools():

    tools = []

    tool_dir = os.path.expanduser(
        "~/ai_agent/tools"
    )

    for file in sorted(os.listdir(tool_dir)):

        if file.endswith(".py"):

            if file.startswith("__"):
                continue

            path = os.path.join(
                tool_dir,
                file
            )

            spec = importlib.util.spec_from_file_location(
                file[:-3],
                path
            )

            module = importlib.util.module_from_spec(
                spec
            )

            spec.loader.exec_module(
                module
            )


            for name in dir(module):

                obj = getattr(module,name)

                if hasattr(obj, "invoke"):

                    if obj not in tools:

                        tools.append(obj)
    
    return tools
