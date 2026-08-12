from fastapi import FastAPI
from pydantic import BaseModel

from ai_agent.router import ToolRouter


app = FastAPI(
    title="Baixin AI Agent API"
)


router = ToolRouter()


class Task(BaseModel):
    tool: str
    input: object


@app.get("/")
def home():
    return {
        "status": "running",
        "agent": "baixin-ai-agent"
    }


@app.post("/call")
def call_agent(task: Task):

    try:

        result = router.call(
            task.tool,
            task.input
        )

        return {
            "success": True,
            "result": result
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }
