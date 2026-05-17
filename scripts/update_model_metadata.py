"""Maintainer-only model metadata refresh helper."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx

PROFILE_PATH = Path(__file__).parents[1] / "pythinker" / "providers" / "model_profiles.json"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _base(provider: str, model_id: str, source: str = "provider_api") -> dict[str, Any]:
    return {"provider": provider, "model_id": model_id, "source": source, "confidence": source}

def parse_anthropic_models(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("data", []):
        row = _base("anthropic", item["id"])
        row.update(input_tokens=item.get("max_input_tokens"), max_output_tokens=item.get("max_tokens"), total_context_tokens=item.get("max_input_tokens"), preferred_api="anthropic_messages", count_tokens_supported=True, runtime_metadata_supported=True)
        rows.append(row)
    return rows

def parse_openrouter_models(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("data", []):
        output = (item.get("top_provider") or {}).get("max_completion_tokens")
        total = item.get("context_length")
        row = _base("openrouter", item["id"])
        row.update(input_tokens=(total - output if total and output else total), max_output_tokens=output, total_context_tokens=total)
        rows.append(row)
    return rows

def parse_gemini_models(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("models", []):
        row = _base("gemini", item["name"].removeprefix("models/"))
        row.update(aliases=item.get("aliases", []), input_tokens=item.get("inputTokenLimit"), max_output_tokens=item.get("outputTokenLimit"), total_context_tokens=item.get("inputTokenLimit"), supports_tools=True, supports_vision=True)
        rows.append(row)
    return rows

def sorted_profile_payload(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {"models": sorted(rows, key=lambda r: (r.get("provider", ""), r.get("model_id", "")))}

def strict_validate(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        for key in ("input_tokens", "max_output_tokens"):
            value = row.get(key)
            if value is not None and value <= 0:
                raise SystemExit(2)

async def fetch_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for attempt, delay in enumerate((1, 2, 4), start=1):
            try:
                response = await client.get(url, headers=headers)
                if response.status_code < 500:
                    response.raise_for_status()
                    return response.json()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
                if attempt == 3:
                    raise
            await asyncio.sleep(delay)
    raise RuntimeError(f"failed to fetch {url}")

def write_profiles(rows: list[dict[str, Any]], path: Path = PROFILE_PATH) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(sorted_profile_payload(rows), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["anthropic", "openrouter", "gemini"])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    rows: list[dict[str, Any]] = []
    if args.provider == "openrouter" or args.all:
        rows += parse_openrouter_models(await fetch_json("https://openrouter.ai/api/v1/models"))
    if args.strict:
        strict_validate(rows)
    if args.dry_run:
        print(json.dumps(sorted_profile_payload(rows), indent=2))
    else:
        write_profiles(rows)
        print(f"wrote {len(rows)} model profile(s)")

if __name__ == "__main__":
    asyncio.run(main())
