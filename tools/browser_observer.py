import json
import requests
import websocket


CDP_URL = "http://127.0.0.1:9222"


def _page():
    pages = requests.get(
        f"{CDP_URL}/json/list",
        proxies={"http": None, "https": None},
        timeout=3,
    ).json()

    for page in pages:
        if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
            return page

    return None


def browser_text():
    page = _page()
    if not page:
        return ""

    ws = websocket.create_connection(
        page["webSocketDebuggerUrl"],
        timeout=5,
    )

    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": "document.body.innerText",
                "returnByValue": True,
            },
        }))

        result = json.loads(ws.recv())

        return (
            result
            .get("result", {})
            .get("result", {})
            .get("value", "")
        )
    finally:
        ws.close()


def browser_elements():
    page = _page()
    if not page:
        return []

    ws = websocket.create_connection(
        page["webSocketDebuggerUrl"],
        timeout=5,
    )

    expression = """
(() => {
    function selector(el) {
        if (el.id) return '#' + CSS.escape(el.id);

        const attrs = ['aria-label', 'name', 'placeholder', 'title'];
        for (const attr of attrs) {
            const value = el.getAttribute(attr);
            if (value) {
                return `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(value)}"]`;
            }
        }

        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role');
        if (role) return `${tag}[role="${CSS.escape(role)}"]`;

        return tag;
    }

    const nodes = document.querySelectorAll(
        'button, input, textarea, select, a, [role="button"], [role="textbox"], [contenteditable="true"]'
    );

    return Array.from(nodes).map((el, i) => ({
        index: i,
        tag: el.tagName.toLowerCase(),
        text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim(),
        aria: el.getAttribute('aria-label') || '',
        role: el.getAttribute('role') || '',
        type: el.getAttribute('type') || '',
        disabled: !!el.disabled,
        selector: selector(el),
    })).filter(x => x.text || x.aria);
})()
"""

    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
            },
        }))

        result = json.loads(ws.recv())

        return (
            result
            .get("result", {})
            .get("result", {})
            .get("value", [])
        )
    finally:
        ws.close()

def browser_click(selector):
    page = _page()
    if not page:
        return False

    ws = websocket.create_connection(
        page["webSocketDebuggerUrl"],
        timeout=5,
    )

    expression = "(function(){const el=document.querySelector(" + json.dumps(selector) + ");if(!el)return false;el.click();return true;})()"

    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
            },
        }))
        result = json.loads(ws.recv())
        return result.get("result", {}).get("result", {}).get("value", False)
    finally:
        ws.close()

def browser_click_text(text):
    text = str(text).strip()
    for element in browser_elements():
        if element.get("text") == text and element.get("selector"):
            return browser_click(element["selector"])
    return False

from langchain_core.tools import tool


@tool
def browser_text_tool():
    """
    读取当前 Chromium 页面可见文字。
    """
    return browser_text()


@tool
def browser_elements_tool():
    """
    读取当前 Chromium 页面可交互元素。
    """
    return browser_elements()


@tool
def browser_click_text_tool(text: str):
    """
    按当前 Chromium 页面上的可见文字点击元素。
    """
    return browser_click_text(text)
