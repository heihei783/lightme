"""Midscene.js 工具 —— AI 驱动的浏览器自动化

通过 Node.js 桥接脚本调用 Midscene.js，实现自然语言控制浏览器。
支持的 action: navigate, click, type, scroll, hover, query

Midscene.js 使用视觉模型（默认 doubao-seed）理解页面截图，
因此能准确定位 UI 元素，无需 CSS 选择器或 XPath。
"""

import json
import os
import subprocess
from pathlib import Path
from langchain_core.tools import tool


def _get_bridge_dir() -> Path:
    """返回 Node.js 桥接脚本所在目录"""
    return Path(__file__).parent / "bridge"


def _get_project_root() -> Path:
    """返回项目根目录"""
    return Path(__file__).parent.parent.parent.parent.parent


def _get_screenshot_dir() -> str:
    """返回截图保存目录，自动创建"""
    d = _get_project_root() / "data" / "browser_screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _load_midscene_config() -> dict:
    """从项目配置中加载 Midscene 所需的 API 密钥和模型配置。

    Midscene.js 底层使用 OpenAI SDK，通过以下环境变量配置:
      - OPENAI_API_KEY   -> VISION_MODEL_API_KEY
      - OPENAI_BASE_URL  -> VISION_MODEL_URL
      - MIDSCENE_MODEL_NAME -> VISION_MODEL_NAME
    """
    try:
        from utils.config_handler import load_configai_config
        config = load_configai_config()
    except Exception:
        config = {}

    result = {
        "OPENAI_API_KEY": config.get("VISION_MODEL_API_KEY", ""),
        "OPENAI_BASE_URL": config.get("VISION_MODEL_URL", ""),
        "MIDSCENE_MODEL_NAME": config.get("VISION_MODEL_NAME", ""),
    }

    # 使用本机 Edge 浏览器（可见模式），默认开启
    if config.get("MIDSCENE_USE_EDGE", True):
        result["MIDSCENE_USE_EDGE"] = "true"
        result["MIDSCENE_CDP_PORT"] = str(config.get("MIDSCENE_CDP_PORT", 9222))
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            result["MIDSCENE_USER_DATA"] = os.path.join(
                localappdata, "Microsoft", "Edge", "User Data"
            )
        else:
            result["MIDSCENE_USER_DATA"] = str(
                _get_project_root() / "data" / "edge_profile"
            )

    return result


def _run_bridge(payload: dict, timeout: int = 60) -> dict:
    """执行 Node.js 桥接脚本并返回结果。"""
    bridge_dir = _get_bridge_dir()
    bridge_script = bridge_dir / "bridge.mjs"

    if not bridge_script.exists():
        return {
            "success": False,
            "error": (
                f"Midscene 桥接脚本不存在: {bridge_script}。"
                f"请先运行: cd {bridge_dir} && npm install && npx playwright install chromium"
            ),
        }

    # 注入截图目录
    payload.setdefault("screenshotDir", _get_screenshot_dir())

    json_input = json.dumps(payload, ensure_ascii=False)

    # 注入视觉模型配置作为环境变量
    env = os.environ.copy()
    midscene_config = _load_midscene_config()
    if midscene_config["OPENAI_API_KEY"]:
        env["OPENAI_API_KEY"] = midscene_config["OPENAI_API_KEY"]
    if midscene_config["OPENAI_BASE_URL"]:
        env["OPENAI_BASE_URL"] = midscene_config["OPENAI_BASE_URL"]
    if midscene_config["MIDSCENE_MODEL_NAME"]:
        env["MIDSCENE_MODEL_NAME"] = midscene_config["MIDSCENE_MODEL_NAME"]

    try:
        result = subprocess.run(
            ["node", str(bridge_script), json_input],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=str(bridge_dir),
            env=env,
        )

        if result.returncode != 0:
            stderr = result.stderr[:1000] if result.stderr else ""
            return {
                "success": False,
                "error": f"桥接脚本执行失败 (exit={result.returncode}): {stderr}",
            }

        try:
            # Midscene.js 可能在 stdout 中额外打印报告路径，提取第一个 JSON 对象
            stdout = result.stdout or ""
            # 找到第一个 { 的位置，从那里开始解析
            idx = stdout.find("{")
            if idx == -1:
                return {"success": False, "error": f"桥接脚本未返回 JSON: {stdout[:200]}"}
            return json.loads(stdout[idx:])
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": f"无法解析桥接脚本输出: {result.stdout[:500]}",
            }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"桥接脚本执行超时 ({timeout}s)"}
    except FileNotFoundError:
        return {
            "success": False,
            "error": "未找到 Node.js。请安装 Node.js 并将其添加到 PATH 环境变量中。",
        }
    except Exception as e:
        return {"success": False, "error": f"执行桥接脚本时出错: {str(e)}"}


def _format_results(results: list) -> str:
    """将桥接结果列表格式化为可读文本"""
    if not results:
        return "操作完成，无返回结果"
    lines = []
    for i, r in enumerate(results, 1):
        action = r.get("action", "?")
        success = "成功" if r.get("success", True) else "失败"
        detail = r.get("detail", "")
        url = r.get("url", "")
        title = r.get("title", "")

        line = f"{i}. [{action}] {success}"
        if url:
            line += f" - URL: {url}"
        if title:
            line += f" - 标题: {title}"
        if detail:
            line += f" - {detail}"
        if r.get("error"):
            line += f" - 错误: {r['error']}"
        lines.append(line)

    screenshots = [r.get("screenshot") for r in results if r.get("screenshot")]
    if screenshots:
        lines.append(f"\n截图文件: {', '.join(screenshots)}")

    return "\n".join(lines)


# ============================================================
# 工具函数
# ============================================================


@tool
def midscene_act(action: str, url: str = "", instruction: str = "",
                 locate: str = "", input_text: str = "") -> str:
    """
    ⚠️ 重要限制：每次调用启动独立浏览器，调用结束后浏览器关闭。多步操作（如导航+搜索+点击）请用 midscene_flow！

    使用 Midscene.js AI 视觉模型执行单个浏览器操作。
    仅适用于单一独立操作（如只打开一个网页、只截一张图）。

    参数 action: 操作类型，可选值:
        - "navigate": 打开网址，需提供 url 参数
        - "click": 点击页面元素，需提供 instruction 描述目标
        - "type": 在输入框中输入文字，需提供 locate(哪个输入框) 和 input_text(输入什么内容)
        - "scroll": 滚动页面，需提供 instruction 描述滚动方向/距离
        - "hover": 悬停在元素上，需提供 instruction 描述目标
        - "query": AI 分析页面内容，需提供 instruction 描述问题
    参数 url: 目标网址（navigate 时必需）
    参数 instruction: 自然语言描述要执行的操作或问题（click/scroll/hover/query 用）
    参数 locate: type 操作专用 — 描述目标输入框位置，如 "页面顶部的搜索框"
    参数 input_text: type 操作专用 — 要输入的文字内容，如 "食贫道"
    """
    valid_actions = {"navigate", "click", "type", "scroll", "hover", "query", "screenshot", "wait"}
    action = action.strip().lower()
    if action not in valid_actions:
        return f"不支持的 action 类型: '{action}'。支持: {', '.join(sorted(valid_actions))}"

    if action == "navigate":
        if not url:
            return "navigate 操作需要提供 url 参数"
        payload = {"actions": [{"action": "navigate", "url": url}]}
    elif action == "type":
        if not locate and not instruction:
            return "type 操作需要提供 locate/input_text 或 instruction 参数"
        act = {"action": "type"}
        if locate:
            act["locate"] = locate
            act["input"] = input_text or instruction
        else:
            act["instruction"] = instruction
        payload = {"actions": [act]}
    elif action == "wait":
        delay = instruction or "2000"
        payload = {"actions": [{"action": "wait", "instruction": delay}]}
    elif action == "screenshot":
        name = instruction or "screenshot"
        payload = {"actions": [{"action": "screenshot", "name": name}]}
    else:
        if not instruction:
            return f"{action} 操作需要提供 instruction 参数（自然语言描述）"
        payload = {"actions": [{"action": action, "instruction": instruction}]}

    result = _run_bridge(payload)

    if not result.get("success", False):
        return f"Midscene 操作失败: {result.get('error', '未知错误')}"

    return _format_results(result.get("results", []))


@tool
def midscene_flow(flow_json: str = "", yaml_path: str = "") -> str:
    """
    ✅ 推荐：执行多步骤浏览器自动化流程。所有操作在同一个浏览器中完成。
    这是完成搜索、填表、数据采集等多步任务的首选工具。

    与 midscene_act 的对比：
    - midscene_act 每次调用启动全新浏览器 → 无法链式操作
    - midscene_flow 一次性执行全部步骤 → 状态连贯，推荐用于多步任务

    参数 flow_json: JSON 格式的操作序列。格式示例:
        {"actions": [
          {"action": "navigate", "url": "https://..."},
          {"action": "click", "instruction": "点击登录按钮"},
          {"action": "type", "instruction": "在用户名框输入admin"},
          {"action": "query", "instruction": "页面上有哪些商品？"},
          {"action": "screenshot", "name": "result"}
        ]}
    参数 yaml_path: Midscene.js YAML 流程文件的绝对路径（与 flow_json 二选一）
    """
    if yaml_path:
        yaml_file = Path(yaml_path)
        if not yaml_file.exists():
            return f"YAML 文件不存在: {yaml_path}"
        try:
            payload = {"yamlFile": str(yaml_file.resolve())}
        except Exception as e:
            return f"读取 YAML 文件出错: {e}"
    elif flow_json:
        try:
            payload = json.loads(flow_json)
        except json.JSONDecodeError as e:
            return f"flow_json JSON 解析失败: {e}。请确保传入合法的 JSON 字符串。"
        if "actions" not in payload:
            return "flow_json 必须包含 'actions' 数组字段"
    else:
        return "请提供 flow_json 或 yaml_path 参数"

    result = _run_bridge(payload, timeout=120)

    if not result.get("success", False):
        return f"Midscene 流程执行失败: {result.get('error', '未知错误')}"

    return _format_results(result.get("results", []))


@tool
def midscene_screenshot(name: str = "screenshot") -> str:
    """
    截取当前浏览器页面的截图并保存到本地。用于视觉分析或记录页面状态。

    参数 name: 截图文件名（不含扩展名），默认 "screenshot"
    """
    if "/" in name or "\\" in name:
        return "截图名称不能包含路径分隔符"

    payload = {"actions": [{"action": "screenshot", "name": name}]}

    result = _run_bridge(payload)

    if not result.get("success", False):
        return f"截图失败: {result.get('error', '未知错误')}"

    return _format_results(result.get("results", []))


# 本技能可用的工具列表
TOOLS = [
    midscene_act,
    midscene_flow,
    midscene_screenshot,
]
