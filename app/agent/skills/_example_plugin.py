"""
示例 Skill 插件 —— 展示如何编写自定义技能插件

"自动加载"原理（一句话）：
  系统启动时自动 import 这个目录下的所有 .py 文件，
  文件里的 skill_registry.register(...) 在 import 时就会执行，技能就注册好了。

你不需要在任何地方手动写 import 语句。

如何使用：
  1. 复制本文件，重命名为你的技能名（如 web_search.py）
  2. 改掉 my_custom_skill 函数体，写入你的逻辑
  3. 修改底部的 skill_registry.register() 参数（name, description, keywords, category）
  4. 重启系统即可

注意事项：
  - 文件名不要以下划线 _ 开头（下划线开头的文件不会被自动加载）
  - 技能名 (name) 必须全局唯一，不能与已有技能重名
"""
from app.agent.skills import Skill, skill_registry


# ===== 你的技能逻辑函数 =====
def my_custom_skill(query: str) -> str:
    """
    你的技能逻辑写在这里。
    Args:
        query: 输入参数
    Returns:
        技能执行结果字符串
    """
    # TODO: 实现你的技能逻辑
    return f"技能执行完成，输入: {query}"


# ===== 模块级注册 =====
# 下面这行代码在 import 时自动执行，技能就注册好了
skill_registry.register(Skill(
    name="my_custom_skill",              # 全局唯一标识
    description="示例自定义技能的描述",    # LLM 据此判断何时使用
    func=my_custom_skill,                 # 指向你的技能函数
    keywords=["示例", "自定义", "demo"],   # 触发关键词
    category="general"                    # 分类: search / execute / analyze / create / general
))
