"""Small, self-contained authentication screens matching the console palette."""

import base64
import hashlib
from html import escape

CSS = """
:root{color-scheme:light;
--accent:#5140df;
--ink:#192c48;
--muted:#738097;
--line:#e3e7ef}
*{box-sizing:border-box}body{margin:0;
background:#f5f6fb;
color:var(--ink);

font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;

font-size:14px;
line-height:1.6;
min-height:100vh;
min-height:100svh;
display:grid;
place-items:center;
padding:32px 20px}
.shell{width:100%;
max-width:420px}.brand{display:flex;
justify-content:center;
align-items:center;

gap:10px;
margin-bottom:28px;
font-size:23px;
font-weight:700;
letter-spacing:-.5px}
.brand-mark{width:34px;
height:34px;
border-radius:10px;
background:var(--accent);
color:white;

display:grid;
place-items:center;
font-size:20px;
font-weight:600}.brand small{font-size:12px;
font-weight:400;

letter-spacing:0;
color:var(--muted);
border-left:1px solid #d5dbe6;
padding-left:12px;
margin-left:3px}
main{background:white;
border:1px solid var(--line);
border-radius:18px;
padding:36px;

box-shadow:0 8px 30px #25345b06}h1{margin:0 0 8px;
font-size:24px;
line-height:1.4;
letter-spacing:-.4px}
.intro{color:var(--muted);
margin:0 0 28px}form{display:grid;
gap:20px}label{display:grid;
gap:8px;

font-weight:500}input:not([type=hidden]){width:100%;
border:1px solid #dce1ec;
border-radius:8px;

padding:11px 12px;
font:inherit;
color:var(--ink);
background:#fff;
min-height:44px;
transition:border-color .15s}
input::placeholder{color:#9aa4b6}input:hover{border-color:#b9c1d2}
input:focus-visible,button:focus-visible,a:focus-visible{outline:3px solid #dcd7ff;
outline-offset:3px}
input:focus{border-color:var(--accent)}button{border:0;
border-radius:8px;
background:var(--accent);

color:white;
font:inherit;
font-weight:600;
min-height:44px;
padding:11px 18px;
cursor:pointer;
width:100%}
button:hover{background:#4432cd}a{color:var(--accent);
text-decoration:none}a:hover{text-decoration:underline}
.secondary{display:block;
text-align:center;
margin-top:18px}.footer{text-align:center;
color:#8c96a8;

font-size:12px;
margin:22px 0 0}.note{margin:0 0 24px;
padding:14px 16px;
border:1px solid var(--line);

border-radius:10px;
background:#fafbfe;
color:#647189}.note strong{color:var(--ink);
font-weight:500}
@media(max-width:480px){body{padding:24px 16px}main{padding:28px 24px}.brand{margin-bottom:22px}}
"""

STYLE_SOURCE = "'sha256-" + base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode() + "'"


def render(title, content):
    introduction = {
        "登录 MemOS": "欢迎回来，登录后继续管理你的记忆与项目。",
        "注册 MemOS": "创建账号，开始管理你的个人记忆空间。",
        "退出登录": "确认退出当前账号？",
    }.get(title, "管理你的 MemOS 工作空间。")
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>'
        + escape(title)
        + "</title><style>"
        + CSS
        + '</style></head><body><div class="shell">'
        '<div class="brand"><span class="brand-mark" aria-hidden="true">M</span>'
        "MemOS<small>记忆控制台</small></div><main><h1>"
        + escape(title)
        + '</h1><p class="intro">'
        + introduction
        + "</p>"
        + content
        + '</main><p class="footer">MemOS · 让记忆连接每一次对话</p></div></body></html>'
    )
