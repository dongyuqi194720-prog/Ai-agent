
from langchain_core.tools import tool

from urllib.request import Request, urlopen

from urllib.parse import quote

from html.parser import HTMLParser

import re

class TextParser(HTMLParser):

    def __init__(self):

        super().__init__()

        self.parts = []

        self.skip = 0

    def handle_starttag(self, tag, attrs):

        if tag.lower() in (

            "script",

            "style",

            "noscript"

        ):

            self.skip += 1

    def handle_endtag(self, tag):

        if tag.lower() in (

            "script",

            "style",

            "noscript"

        ):

            self.skip = max(

                0,

                self.skip - 1

            )

    def handle_data(self, data):

        if self.skip == 0:

            text = data.strip()

            if text:

                self.parts.append(text)

@tool

def web_fetch(url: str):

    """

    获取网页正文文本

    """

    try:

        req = Request(

            url,

            headers={

                "User-Agent":

                "Mozilla/5.0 AI-Programmer"

            }

        )

        with urlopen(

            req,

            timeout=20

        ) as response:

            raw = response.read()

            charset = (

                response.headers.get_content_charset()

                or "utf-8"

            )

            html = raw.decode(

                charset,

                errors="ignore"

            )

        parser = TextParser()

        parser.feed(html)

        text = "\n".join(

            parser.parts

        )

        text = re.sub(

            r"\s+",

            " ",

            text

        )

        return text[:30000]

    except Exception as e:

        return (

            "网页抓取失败: "

            + str(e)

        )

