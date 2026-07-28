"""러너 6종이 공유하는 최소 유틸 — argparse(--sample/--full)/JSONL 로드/리포트 저장.

evals/runners/의 다른 스크립트는 이 파일을 재사용한다(6번 중복 방지).
--full/--all은 guard_eval_cost.py 훅이 Bash에서 차단하므로 실제로 무거운
실행은 사람이 직접 한다 — 여기서는 플래그만 파싱한다.
"""
import argparse
import datetime
import json
import pathlib

REPORTS_DIR = pathlib.Path("evals/reports")


def parse_args(default_sample: int = 20) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=int, default=default_sample)
    p.add_argument("--full", action="store_true")
    p.add_argument("--all", action="store_true")
    return p.parse_args()


def load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def select_sample(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.full or args.all:
        return rows
    return rows[: args.sample]


def write_report(name: str, data: dict) -> pathlib.Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{name}.json"
    data = {**data, "generated_at": datetime.datetime.utcnow().isoformat() + "Z"}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
