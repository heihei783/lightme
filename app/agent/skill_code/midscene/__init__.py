"""Midscene.js 技能模块 —— AI 驱动的浏览器自动化

基于字节跳动 Midscene.js，使用视觉模型理解和操控浏览器。
底层通过 Node.js 桥接脚本调用 @midscene/web + Playwright。
"""

from app.agent.skill_code.midscene.tools import TOOLS

__all__ = ["TOOLS"]
