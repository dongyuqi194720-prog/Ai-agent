from langchain_core.tools import tool
import json
import os
import urllib.request
import websocket


@tool
def browser_state():
    """
    读取当前 Chromium 页面结构化状态。
    返回页面标题、URL、可见文本、输入框、按钮和链接。
    """
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )

        with opener.open(
            "http://127.0.0.1:9222/json",
            timeout=5
        ) as response:
            targets = json.loads(
                response.read().decode("utf-8")
            )

        page = next(
            (
                target
                for target in targets
                if target.get("type") == "page"
            ),
            None
        )

        if not page:
            return "浏览器状态读取失败: 未找到 page target"

        ws_url = page.get("webSocketDebuggerUrl")

        if not ws_url:
            return "浏览器状态读取失败: 缺少 WebSocket 地址"

        ws = websocket.create_connection(
            ws_url,
            suppress_origin=True,
            timeout=10
        )

        expression = r"""
(() => {
    const visible = el => {
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.display !== "none" &&
               s.visibility !== "hidden" &&
               r.width > 0 &&
               r.height > 0;
    };

    const text = el =>
        (el.innerText || el.textContent || "")
            .replace(/\s+/g, " ")
            .trim();

    return {
        title: document.title,
        url: location.href,
        visible_text: document.body
            ? text(document.body).slice(0, 20000)
            : "",

        inputs: [...document.querySelectorAll(
            "input, textarea, select"
        )]
        .filter(visible)
        .slice(0, 100)
        .map(el => ({
            tag: el.tagName.toLowerCase(),
            type: el.type || "",
            name: el.name || "",
            id: el.id || "",
            placeholder: el.placeholder || "",
            value: el.value || ""
        })),

        buttons: [...document.querySelectorAll(
            "button, input[type=button], input[type=submit]"
        )]
        .filter(visible)
        .slice(0, 100)
        .map(el => ({
            tag: el.tagName.toLowerCase(),
            type: el.type || "",
            id: el.id || "",
            text: text(el).slice(0, 300)
        })),

        links: [...document.querySelectorAll("a")]
        .filter(visible)
        .slice(0, 200)
        .map(el => ({
            text: text(el).slice(0, 300),
            href: el.href || ""
        }))
    };
})()
"""

        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True
            }
        }))

        while True:
            message = json.loads(ws.recv())

            if message.get("id") == 1:
                break

        ws.close()

        result = (
            message
            .get("result", {})
            .get("result", {})
            .get("value")
        )

        if result is None:
            return "浏览器状态读取失败: DOM 返回为空"

        return json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )

    except Exception as e:
        return f"浏览器状态读取失败: {e}"
