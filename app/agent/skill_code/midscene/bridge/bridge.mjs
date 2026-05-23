/**
 * LightMe Midscene Bridge
 * =======================
 * Node.js 桥接脚本 —— 接收 JSON 操作指令，使用 Midscene.js (AI 视觉模型)
 * 驱动 Playwright 浏览器执行自动化任务。
 *
 * 用法: node bridge.mjs '<JSON payload>'
 *
 * JSON payload 格式:
 *   { "actions": [...], "screenshotDir": "path/to/screenshots" }
 *
 * 每个 action:
 *   { "action": "navigate", "url": "https://..." }
 *   { "action": "click", "instruction": "自然语言描述目标" }
 *   { "action": "type",  "instruction": "自然语言描述输入位置和内容" }
 *   { "action": "scroll", "instruction": "自然语言描述滚动方式" }
 *   { "action": "hover", "instruction": "自然语言悬停目标" }
 *   { "action": "query", "instruction": "关于页面内容的问题" }
 *   { "action": "screenshot", "name": "文件名(不含扩展名)" }
 *
 * 也可传入 YAML 文件路径:
 *   { "yamlFile": "/absolute/path/to/flow.yaml" }
 */

import { existsSync, mkdirSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { chromium } from "playwright";

// ── Midscene 导入（可选，未安装时回退到纯 Playwright 模式） ──
let PlaywrightAgent = null;
let midsceneAvailable = false;
try {
  const midscene = await import("@midscene/web");
  PlaywrightAgent = midscene.PlaywrightAgent || midscene.default?.PlaywrightAgent;
  if (PlaywrightAgent) {
    midsceneAvailable = true;
  }
} catch (e) {
  // @midscene/web 未安装或导入失败，使用纯 Playwright 回退模式
  console.error("[bridge] Midscene.js 未安装，使用基础 Playwright 模式:", e.message);
}

// ── 浏览器配置 ──
// 通过环境变量控制（Python 侧注入）:
//   MIDSCENE_USE_EDGE     - 设为 "true" 连接本机 Edge 浏览器
//   MIDSCENE_CDP_PORT     - Edge 远程调试端口（默认 9222）
//   MIDSCENE_USER_DATA    - Edge 用户数据目录（仅首次启动 Edge 时使用）
//
// 工作原理:
//   1. 尝试连接 localhost:<CDP_PORT> 上已运行的 Edge
//   2. 如果没连上，自动启动 Edge 并开启 CDP 调试端口
//   3. 这样你的 Edge 登录态、书签都在，也不需要关掉 Edge
const USE_EDGE = process.env.MIDSCENE_USE_EDGE === "true";
const CDP_PORT = process.env.MIDSCENE_CDP_PORT || "9222";
const USER_DATA_DIR = process.env.MIDSCENE_USER_DATA || "";

// ── 模型配置 ──
//   MIDSCENE_MODEL_NAME  - 模型名称 (默认 doubao-seed-2-0-mini-260428)
//   OPENAI_API_KEY       - API 密钥 (Midscene 底层用 OpenAI SDK)
//   OPENAI_BASE_URL      - API 端点 (Midscene 底层用 OpenAI SDK)
const MODEL_CONFIG = {
  modelName: process.env.MIDSCENE_MODEL_NAME || "doubao-seed-2-0-mini-260428",
  apiKey: process.env.OPENAI_API_KEY || "",
  baseUrl: process.env.OPENAI_BASE_URL || "https://ark.cn-beijing.volces.com/api/v3",
};

// ═══════════════════════════════════════════════════════════
// 主函数
// ═══════════════════════════════════════════════════════════

async function main() {
  const rawInput = process.argv[2];
  if (!rawInput) {
    output({ success: false, error: "缺少 JSON 输入参数" });
    return;
  }

  let payload;
  try {
    payload = JSON.parse(rawInput);
  } catch (e) {
    output({ success: false, error: `JSON 解析失败: ${e.message}` });
    return;
  }

  const screenshotDir = payload.screenshotDir || ".";
  const actions = payload.actions || [];

  if (!actions.length && !payload.yamlFile) {
    output({ success: false, error: "payload 中没有 actions 数组" });
    return;
  }

  // 创建截图目录
  if (!existsSync(screenshotDir)) {
    mkdirSync(screenshotDir, { recursive: true });
  }

  // 启动/连接浏览器
  let browser = null;
  let context;
  let page;

  if (USE_EDGE) {
    // Edge 模式：通过 CDP 连接正在运行的 Edge
    // Edge 需要已用 --remote-debugging-port=9222 启动（任务栏快捷方式已配置）
    const cdpUrl = `http://localhost:${CDP_PORT}`;
    let connected = false;

    try {
      const res = await fetch(`${cdpUrl}/json/version`);
      if (res.ok) {
        browser = await chromium.connectOverCDP(cdpUrl);
        const pages = browser.contexts()[0]?.pages() || [];
        page = pages[0] || await browser.contexts()[0]?.newPage();
        if (page) {
          connected = true;
          console.error(`[bridge] 已连接到你的 Edge 浏览器`);
        }
      }
    } catch {
      // CDP 端口不可用
    }

    if (!connected) {
      console.error(`[bridge] Edge 未开启调试端口。请用任务栏 Edge 图标重新打开浏览器。`);
    }
  }

  // Chromium 回退模式
  if (!page) {
    try {
      browser = await chromium.launch({
        headless: true,
        args: ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
      });
    } catch (e) {
      output({
        success: false,
        error: `无法启动浏览器: ${e.message}`,
      });
      return;
    }
    context = await browser.newContext({
      viewport: { width: 1280, height: 720 },
      locale: "zh-CN",
      userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    });
    page = await context.newPage();
  }

  // Edge 模式不需要反检测（真实浏览器），Chromium 模式需要
  if (!USE_EDGE) {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => false });
      Object.defineProperty(navigator, "plugins", {
        get: () => [1, 2, 3, 4, 5],
      });
      Object.defineProperty(navigator, "languages", {
        get: () => ["zh-CN", "zh", "en"],
      });
      window.chrome = { runtime: {} };
      // 覆盖权限查询，避免暴露自动化
      const originalQuery = window.navigator.permissions.query;
      window.navigator.permissions.query = (parameters) =>
        parameters.name === "notifications"
          ? Promise.resolve({ state: Notification.permission })
          : originalQuery(parameters);
    });
  }

  // 如果 Midscene 可用，创建 AI Agent
  let agent = null;
  if (midsceneAvailable && PlaywrightAgent) {
    try {
      agent = new PlaywrightAgent(page, {
        model: {
          name: MODEL_CONFIG.modelName,
          // 部分版本的 Midscene 需要这些配置
          ...(MODEL_CONFIG.apiKey ? { apiKey: MODEL_CONFIG.apiKey } : {}),
          ...(MODEL_CONFIG.baseUrl ? { baseUrl: MODEL_CONFIG.baseUrl } : {}),
        },
      });
    } catch (e) {
      console.error("[bridge] 初始化 Midscene Agent 失败:", e.message);
      midsceneAvailable = false;
    }
  }

  // 执行操作序列
  const results = [];
  let hasNavigated = false;

  for (let i = 0; i < actions.length; i++) {
    const action = actions[i];
    try {
      const result = await executeAction(page, agent, action, screenshotDir, hasNavigated);
      if (result) {
        results.push(result);
        if (action.action === "navigate") hasNavigated = true;
      }
    } catch (e) {
      results.push({
        index: i,
        action: action.action,
        success: false,
        error: e.message,
      });
      // 遇到严重错误时终止后续操作
      if (e.message?.includes("Target closed") || e.message?.includes("has been closed")) {
        results.push({ action: "error", detail: "浏览器已关闭，终止后续操作" });
        break;
      }
    }
  }

  if (USE_EDGE) {
    // CDP 模式：只关标签页，不关浏览器（留给用户继续用）
    try { await page.close(); } catch {}
  } else if (browser) {
    await browser.close();
  } else if (context) {
    await context.close();
  }
  output({ success: true, results, mode: midsceneAvailable ? "midscene" : "playwright" });
}

// ═══════════════════════════════════════════════════════════
// 操作分发
// ═══════════════════════════════════════════════════════════

async function executeAction(page, agent, action, screenshotDir, hasNavigated) {
  const { action: type, url, instruction, name } = action;

  switch (type) {
    case "navigate":
      return await doNavigate(page, url);

    case "click":
      return await doClick(page, agent, instruction, hasNavigated);

    case "type":
      return await doType(page, agent, instruction, hasNavigated, action.locate, action.input);

    case "scroll":
      return await doScroll(page, agent, instruction, hasNavigated);

    case "hover":
      return await doHover(page, agent, instruction, hasNavigated);

    case "screenshot":
      return await doScreenshot(page, screenshotDir, name || "screenshot");

    case "query":
      return await doQuery(page, agent, instruction, hasNavigated);

    case "wait":
      return await doWait(instruction);

    default:
      return { action: type, success: false, error: `不支持的操作类型: ${type}` };
  }
}

// ── 具体操作实现 ──

async function doNavigate(page, url) {
  if (!url) return { action: "navigate", success: false, error: "缺少 url 参数" };
  const fullUrl = url.startsWith("http") ? url : `https://${url}`;
  await page.goto(fullUrl, { waitUntil: "domcontentloaded", timeout: 45000 });
  const title = await page.title();
  return {
    action: "navigate",
    success: true,
    url: fullUrl,
    title,
    detail: `已导航到: ${title}`,
  };
}

async function doClick(page, agent, instruction, hasNavigated) {
  if (!instruction) return { action: "click", success: false, error: "缺少 instruction" };

  if (!hasNavigated) {
    return { action: "click", success: false, error: "请先使用 navigate 打开目标网页" };
  }

  if (agent) {
    // Midscene AI 模式：通过视觉模型识别元素并点击
    await agent.aiTap(instruction);
    return { action: "click", success: true, detail: `AI 点击: ${instruction}` };
  }

  // Playwright 回退模式：尝试通过文本内容匹配
  try {
    const el = page.getByText(instruction, { exact: false }).first();
    await el.click({ timeout: 10000 });
    return { action: "click", success: true, detail: `点击: ${instruction}` };
  } catch {
    return { action: "click", success: false, error: `未找到可点击的元素: "${instruction}"。建议安装 Midscene.js 使用 AI 视觉定位。` };
  }
}

async function doType(page, agent, instruction, hasNavigated, locate, input) {
  if (!instruction && !locate) return { action: "type", success: false, error: "缺少 instruction 或 locate 参数" };

  if (!hasNavigated) {
    return { action: "type", success: false, error: "请先使用 navigate 打开目标网页" };
  }

  // 分离 locate 和 input: 优先用显式字段，否则从 instruction 中解析
  const locatePrompt = locate || instruction;
  const inputText = input || instruction;

  if (agent) {
    // Midscene AI 模式：aiInput(value, locatePrompt) — value 在前, locate 在后
    await agent.aiInput(inputText, locatePrompt);
    return { action: "type", success: true, detail: `输入: "${inputText}" → ${locatePrompt}` };
  }

  // Playwright 回退模式：聚焦第一个输入框并输入
  try {
    const input = page.locator('input:visible, textarea:visible, [contenteditable="true"]').first();
    await input.fill(inputText, { timeout: 10000 });
    return { action: "type", success: true, detail: `输入: ${inputText}` };
  } catch {
    return { action: "type", success: false, error: `未找到可见输入框。建议安装 Midscene.js 使用 AI 视觉定位。` };
  }
}

async function doScroll(page, agent, instruction, hasNavigated) {
  if (!hasNavigated) {
    return { action: "scroll", success: false, error: "请先使用 navigate 打开目标网页" };
  }

  if (agent && instruction) {
    await agent.aiScroll(instruction);
    return { action: "scroll", success: true, detail: `AI 滚动: ${instruction}` };
  }

  // Playwright 回退模式
  const lower = (instruction || "").toLowerCase();
  if (lower.includes("顶部") || lower.includes("top")) {
    await page.evaluate(() => window.scrollTo(0, 0));
  } else if (lower.includes("底部") || lower.includes("bottom")) {
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  } else if (lower.includes("下") || lower.includes("down")) {
    await page.evaluate(() => window.scrollBy(0, 500));
  } else if (lower.includes("上") || lower.includes("up")) {
    await page.evaluate(() => window.scrollBy(0, -500));
  } else {
    await page.evaluate(() => window.scrollBy(0, 500));
  }
  return { action: "scroll", success: true, detail: instruction ? `滚动: ${instruction}` : "向下滚动一屏" };
}

async function doHover(page, agent, instruction, hasNavigated) {
  if (!instruction) return { action: "hover", success: false, error: "缺少 instruction" };

  if (!hasNavigated) {
    return { action: "hover", success: false, error: "请先使用 navigate 打开目标网页" };
  }

  if (agent) {
    await agent.aiHover(instruction);
    return { action: "hover", success: true, detail: `AI 悬停: ${instruction}` };
  }

  // Playwright 回退模式
  try {
    const el = page.getByText(instruction, { exact: false }).first();
    await el.hover({ timeout: 10000 });
    return { action: "hover", success: true, detail: `悬停: ${instruction}` };
  } catch {
    return { action: "hover", success: false, error: `未找到可悬停的元素: "${instruction}"` };
  }
}

async function doScreenshot(page, dir, name) {
  const filename = `${name.replace(/[/\\:*?"<>|]/g, "_")}.png`;
  const filepath = join(dir, filename);
  await page.screenshot({ path: filepath, fullPage: true });
  return {
    action: "screenshot",
    success: true,
    screenshot: filepath,
    detail: `截图已保存: ${filename}`,
  };
}

async function doQuery(page, agent, instruction, hasNavigated) {
  if (!instruction) return { action: "query", success: false, error: "缺少 instruction" };

  if (!hasNavigated) {
    return { action: "query", success: false, error: "请先使用 navigate 打开目标网页" };
  }

  if (agent) {
    const answer = await agent.aiQuery(instruction);
    return {
      action: "query",
      success: true,
      detail: answer,
    };
  }

  // Playwright 回退模式：获取页面基本信息
  try {
    const title = await page.title();
    const url = page.url();
    const text = await page.evaluate(() => document.body?.innerText?.substring(0, 2000) || "");
    return {
      action: "query",
      success: true,
      title,
      url,
      detail: `页面标题: ${title}\n页面内容片段:\n${text}`,
    };
  } catch (e) {
    return { action: "query", success: false, error: `查询失败: ${e.message}` };
  }
}

async function doWait(instruction) {
  const ms = parseInt(instruction) || 1000;
  const delay = Math.min(ms, 10000);
  await new Promise((r) => setTimeout(r, delay));
  return { action: "wait", success: true, detail: `等待 ${delay}ms` };
}

// ═══════════════════════════════════════════════════════════
// 输出
// ═══════════════════════════════════════════════════════════

function output(obj) {
  process.stdout.write(JSON.stringify(obj, null, 2));
}

main().catch((e) => {
  output({ success: false, error: `未捕获的异常: ${e.message}` });
  process.exit(1);
});
