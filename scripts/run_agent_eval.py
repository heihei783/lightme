"""
Run LightMe Agent evaluation tasks.

Default mode validates the task set and writes a dry-run report. Use --execute
to call the Agent. The output is written to data/eval/latest_results.json and a
CSV file for the RedRock assessment report.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage

from app.agent.agent_graph import run_agent
from utils.path_tool import get_abs_path


TASKS_PATH = Path(get_abs_path("data/eval/agent_eval_tasks.json"))
RESULTS_PATH = Path(get_abs_path("data/eval/latest_results.json"))
CSV_PATH = Path(get_abs_path("data/eval/latest_results.csv"))


def load_tasks() -> List[Dict[str, Any]]:
    with TASKS_PATH.open("r", encoding="utf-8") as f:
        tasks = json.load(f)
    if not isinstance(tasks, list):
        raise ValueError("evaluation tasks must be a JSON list")
    return tasks


def keyword_score(output: str, keywords: List[str]) -> float:
    if not keywords:
        return 1.0
    hits = sum(1 for kw in keywords if kw.lower() in output.lower())
    return hits / len(keywords)


def run_task(task: Dict[str, Any], mode: str, execute: bool) -> Dict[str, Any]:
    started = time.time()
    if execute:
        session_id = f"eval_{mode}_{task['id']}"
        output = run_agent([HumanMessage(content=task["prompt"])], session_id=session_id)
        status = "completed"
    else:
        output = "DRY_RUN: task definition validated; use --execute to run the Agent."
        status = "dry_run"
    score = keyword_score(output, task.get("expected_keywords", []))
    return {
        "id": task["id"],
        "category": task.get("category", ""),
        "mode": mode,
        "status": status,
        "score": score,
        "success": score >= 0.6 if execute else None,
        "duration_seconds": round(time.time() - started, 3),
        "expected_keywords": task.get("expected_keywords", []),
        "output_preview": output[:500],
    }


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    executed = [r for r in results if r["status"] != "dry_run"]
    if not executed:
        return {
            "task_count": len(results),
            "executed_count": 0,
            "success_rate": None,
            "avg_score": None,
            "avg_duration_seconds": None,
        }
    return {
        "task_count": len(results),
        "executed_count": len(executed),
        "success_rate": sum(1 for r in executed if r["success"]) / len(executed),
        "avg_score": sum(r["score"] for r in executed) / len(executed),
        "avg_duration_seconds": sum(r["duration_seconds"] for r in executed) / len(executed),
    }


def write_outputs(payload: Dict[str, Any]) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    rows = payload["results"]
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "category",
                "mode",
                "status",
                "score",
                "success",
                "duration_seconds",
                "output_preview",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in writer.fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LightMe Agent evaluation tasks.")
    parser.add_argument("--mode", choices=["planner", "baseline"], default="planner")
    parser.add_argument("--execute", action="store_true", help="Actually call the Agent instead of dry-running.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of tasks; 0 means all tasks.")
    args = parser.parse_args()

    tasks = load_tasks()
    if len(tasks) < 20:
        raise ValueError("RedRock assessment requires at least 20 evaluation tasks")
    selected = tasks[: args.limit] if args.limit else tasks
    results = [run_task(task, args.mode, args.execute) for task in selected]
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": args.mode,
        "execute": args.execute,
        "summary": summarize(results),
        "results": results,
    }
    write_outputs(payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {RESULTS_PATH}")
    print(f"Wrote {CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
