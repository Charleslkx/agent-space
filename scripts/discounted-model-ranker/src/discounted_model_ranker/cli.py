#!/usr/bin/env python3
"""Find the top 30 discounted OpenRouter language models by AA Intelligence Index."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
OPENROUTER_ENDPOINTS = "https://openrouter.ai/api/v1/models/{}/endpoints"
AA_MODELS = "https://artificialanalysis.ai/api/v2/language/models/free"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = Path.cwd() / "discounted_models.log"


def get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    command = [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--retry",
        "3",
        "--retry-all-errors",
        "--connect-timeout",
        "10",
        "--max-time",
        "60",
        url,
    ]
    header_input = "".join(f"{key}: {value}\n" for key, value in (headers or {}).items())
    if header_input:
        command.extend(["-H", "@-"])
    try:
        response = subprocess.run(
            command, input=header_input, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"请求失败：{url}：{error.stderr.strip()}") from None
    return json.loads(response.stdout)


def load_api_key() -> str:
    for name in ("AA_API_KEY", "X_API_KEY", "x-api-key"):
        if value := os.environ.get(name):
            return value
    for path in dict.fromkeys((Path.cwd() / ".env", PROJECT_ROOT / ".env")):
        if not path.is_file():
            continue
        match = re.search(
            r'^\s*x-api-key\s*[:=]\s*["\']?([^"\'\s]+)',
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match:
            return match.group(1)
    raise ValueError("请设置 AA_API_KEY，或在当前目录的 .env 中配置 x-api-key")


def normalized(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def aliases(name: str, slug: str) -> set[str]:
    short_slug = slug.rsplit("/", 1)[-1]
    short_name = name.rsplit(":", 1)[-1].strip()
    base_name = re.sub(r"\s*\([^)]*\)\s*$", "", short_name)
    values = (name, short_name, base_name, short_slug)
    return {
        alias
        for value in values
        for alias in (
            normalized(value),
            "".join(sorted(re.findall(r"[a-z0-9]+", value.casefold()))),
        )
    }


def is_discount(value: object) -> bool:
    return isinstance(value, (int, float)) and 0 < value < 1


def discounted_model(model: dict) -> dict | None:
    slug = model["canonical_slug"]
    url = OPENROUTER_ENDPOINTS.format(urllib.parse.quote(slug, safe="/"))
    endpoints = get_json(url)["data"]["endpoints"]
    discounted = []
    for endpoint in endpoints:
        pricing = endpoint.get("pricing", {})
        discount = pricing.get("discount")
        if is_discount(discount):
            discounted.append(
                {
                    "provider": endpoint.get("provider_name"),
                    "endpoint": endpoint.get("name"),
                    "discount_percent": round(discount * 100, 2),
                    "input_price_per_token": float(pricing.get("prompt", 0)),
                    "output_price_per_token": float(pricing.get("completion", 0)),
                }
            )
    if not discounted:
        return None
    return {**model, "discounted_endpoints": discounted}


def artificial_analysis_models(api_key: str) -> list[dict]:
    models = []
    page = 1
    while True:
        payload = get_json(f"{AA_MODELS}?page={page}", {"x-api-key": api_key})
        models.extend(payload["data"])
        if not payload["pagination"]["has_more"]:
            return models
        page += 1


def match_models(
    discounted: list[dict], ranked_aa: list[dict]
) -> tuple[list[dict], list[dict], int]:
    aa_by_alias: dict[str, list[dict]] = {}
    for rank, model in enumerate(ranked_aa, 1):
        model["aa_rank"] = rank
        for alias in aliases(model["name"], model["slug"]):
            aa_by_alias.setdefault(alias, []).append(model)

    matches = []
    unmatched = []
    for model in discounted:
        candidates = {
            candidate["id"]: candidate
            for alias in aliases(model["name"], model["canonical_slug"])
            for candidate in aa_by_alias.get(alias, [])
        }
        if not candidates:
            unmatched.append(
                {
                    "name": model["name"],
                    "canonical_slug": model["canonical_slug"],
                    "best_discount_percent": max(
                        endpoint["discount_percent"]
                        for endpoint in model["discounted_endpoints"]
                    ),
                }
            )
            continue
        aa_model = max(
            candidates.values(),
            key=lambda item: item["evaluations"].get("artificial_analysis_intelligence_index") or -1,
        )
        # Find the best discount endpoint to get pricing
        best_endpoint = max(
            model["discounted_endpoints"],
            key=lambda ep: ep["discount_percent"],
        )
        matches.append(
            {
                "name": aa_model["name"],
                "creator": aa_model["model_creator"]["name"],
                "intelligence_index": aa_model["evaluations"].get(
                    "artificial_analysis_intelligence_index"
                ),
                "aa_rank": aa_model["aa_rank"],
                "openrouter_ids": model["ids"],
                "canonical_slug": model["canonical_slug"],
                "best_discount_percent": best_endpoint["discount_percent"],
                "input_price_per_million_tokens": round(best_endpoint["input_price_per_token"] * 1_000_000, 4),
                "output_price_per_million_tokens": round(best_endpoint["output_price_per_token"] * 1_000_000, 4),
                "discounted_endpoints": model["discounted_endpoints"],
            }
        )
    matches.sort(
        key=lambda item: (item["intelligence_index"] or -1, -item["aa_rank"]), reverse=True
    )
    for rank, model in enumerate(matches[:30], 1):
        model["discount_rank"] = rank
    return matches[:30], unmatched, len(matches)


def write_log(result: dict, path: Path = LOG_PATH, timestamp: str | None = None) -> None:
    entry = json.dumps(
        {
            "timestamp": timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
            "result": result,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    # ponytail: single-process rotation; add a file lock if concurrent runs become normal.
    temporary = path.with_suffix(".log.tmp")
    temporary.write_text("\n".join([*lines[-6:], entry]) + "\n", encoding="utf-8")
    temporary.replace(path)


def self_test() -> None:
    assert normalized("xAI / Grok-4.1") == "xaigrok41"
    assert "gpt54" in aliases("GPT-5.4 (Reasoning)", "openai/gpt-5.4")
    assert "gemini37flash" in aliases("Google: Gemini 3.7 Flash", "google/gemini-3.7-flash")
    assert aliases("Llama 3.3 70B Instruct", "x") & aliases("Llama 3.3 Instruct 70B", "y")
    assert not is_discount(0) and is_discount(0.2) and not is_discount(1)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "history.log"
        for query in range(8):
            write_log({"query": query}, path, str(query))
        records = [json.loads(line) for line in path.read_text().splitlines()]
        assert [record["timestamp"] for record in records] == [str(i) for i in range(1, 8)]
    print("self-test passed")


def main() -> None:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return

    raw_models = get_json(OPENROUTER_MODELS)["data"]
    by_slug: dict[str, dict] = {}
    for model in raw_models:
        slug = model.get("canonical_slug") or model["id"]
        if slug not in by_slug:
            by_slug[slug] = {"name": model["name"], "canonical_slug": slug, "ids": []}
        by_slug[slug]["ids"].append(model["id"])

    with ThreadPoolExecutor(max_workers=16) as pool:
        discounted = [result for result in pool.map(discounted_model, by_slug.values()) if result]

    aa_models = artificial_analysis_models(load_api_key())
    ranked_aa = sorted(
        aa_models,
        key=lambda model: model["evaluations"].get("artificial_analysis_intelligence_index") or -1,
        reverse=True,
    )
    matches, unmatched, matched_count = match_models(discounted, ranked_aa)
    result = {
        "sources": {"openrouter": OPENROUTER_MODELS, "artificial_analysis": AA_MODELS},
        "summary": {
            "openrouter_models": len(raw_models),
            "canonical_models_checked": len(by_slug),
            "discounted_models": len(discounted),
            "artificial_analysis_models": len(aa_models),
            "matched_models": matched_count,
            "returned_models": len(matches),
        },
        "models": matches,
        "unmatched_discounted_models": unmatched,
    }
    write_log(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
