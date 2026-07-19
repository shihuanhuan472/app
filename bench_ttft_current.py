#!/usr/bin/env python3
"""
OpenAI-compatible streaming TTFT benchmark for vLLM.

Measures:
  - TTFB: request start -> first response byte / first iter_lines event
  - First SSE event: request start -> first "data:" SSE payload
  - TTFT: request start -> first non-empty choices[0].delta.content

The script is designed to avoid the common mistake of treating the first SSE
role/empty-delta frame as the first generated token.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import string
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_MODEL = "/data/models/Qwen3-VL-32B-Instruct"
FILLER = "The weather today is quite pleasant and the birds are singing. "


@dataclass
class StreamTiming:
    ok: bool
    status_code: int | None
    error: str | None
    headers_ms: float | None
    ttfb_ms: float | None
    first_sse_ms: float | None
    ttft_ms: float | None
    first_token_text: str | None
    sse_events_before_token: int
    response_id: str | None
    finish_reason: str | None


def pct(values: list[float], percentile: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def random_nonce(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def request_json(
    method: str,
    url: str,
    *,
    api_key: str,
    timeout: float,
    **kwargs: Any,
) -> requests.Response:
    headers = kwargs.pop("headers", {})
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return requests.request(method, url, headers=headers, timeout=timeout, **kwargs)


def check_server(base_url: str, api_key: str, timeout: float) -> None:
    url = f"{base_url.rstrip('/')}/v1/models"
    response = request_json("GET", url, api_key=api_key, timeout=timeout)
    response.raise_for_status()
    models = response.json()
    print("[server] /v1/models ok")
    if isinstance(models, dict) and models.get("data"):
        first = models["data"][0]
        model_id = first.get("id") if isinstance(first, dict) else first
        print(f"[server] first model: {model_id}")


def count_prompt_tokens_chat(
    base_url: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    timeout: float,
) -> int | None:
    """
    Uses vLLM's /tokenize endpoint when available.
    This avoids warming generation cache with an extra chat completion request.
    """
    url = f"{base_url.rstrip('/')}/tokenize"
    payload = {
        "model": model,
        "messages": messages,
        "add_generation_prompt": True,
    }

    try:
        response = request_json("POST", url, api_key=api_key, timeout=timeout, json=payload)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None

    if isinstance(data, dict):
        if isinstance(data.get("tokens"), list):
            return len(data["tokens"])
        if isinstance(data.get("token_ids"), list):
            return len(data["token_ids"])
        if isinstance(data.get("input_ids"), list):
            return len(data["input_ids"])
        if isinstance(data.get("count"), int):
            return data["count"]
    return None


def count_prompt_tokens_nonstream(
    base_url: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    timeout: float,
) -> int | None:
    """
    Fallback token counting through non-stream chat completion.
    Warning: this performs a real generation request and can warm prefix cache.
    """
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 1,
        "temperature": 0.0,
        "stream": False,
    }
    response = request_json("POST", url, api_key=api_key, timeout=timeout, json=payload)
    response.raise_for_status()
    data = response.json()
    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens")
    return int(prompt_tokens) if prompt_tokens is not None else None


def make_prompt(target_tokens: int, tokens_per_filler: float, cache_mode: str) -> str:
    repeats = max(1, int(target_tokens / max(tokens_per_filler, 1)))
    body = FILLER * repeats

    if cache_mode == "bust-prefix":
        # Put the random value at the very beginning to prevent long prefix reuse.
        prefix = f"nonce={random_nonce(24)}\n"
    elif cache_mode == "bust-suffix":
        # Keeps the long prefix identical; useful when you want to observe prefix cache behavior.
        prefix = ""
        body = body + f"\nnonce={random_nonce(24)}\n"
    else:
        prefix = ""

    return prefix + body + "\n\nSummarize the text above in one short sentence."


def parse_stream_for_first_token(
    base_url: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> StreamTiming:
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    start = time.perf_counter()
    headers_at: float | None = None
    first_line_at: float | None = None
    first_sse_at: float | None = None
    first_token_at: float | None = None
    first_token_text: str | None = None
    sse_events = 0
    response_id: str | None = None
    finish_reason: str | None = None

    try:
        with requests.post(
            url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=timeout,
        ) as response:
            headers_at = time.perf_counter()
            status_code = response.status_code
            response.raise_for_status()

            for raw_line in response.iter_lines(decode_unicode=True):
                now = time.perf_counter()
                if first_line_at is None:
                    first_line_at = now

                if not raw_line:
                    continue
                if not raw_line.startswith("data: "):
                    continue

                if first_sse_at is None:
                    first_sse_at = now

                payload_text = raw_line[len("data: ") :]
                if payload_text == "[DONE]":
                    break

                sse_events += 1

                try:
                    event = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue

                if response_id is None:
                    response_id = event.get("id")

                choices = event.get("choices") or []
                if not choices:
                    continue

                choice = choices[0]
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}
                content = delta.get("content")

                if content:
                    first_token_at = now
                    first_token_text = content
                    break

            return StreamTiming(
                ok=first_token_at is not None,
                status_code=status_code,
                error=None if first_token_at is not None else "stream ended before non-empty content token",
                headers_ms=(headers_at - start) * 1000 if headers_at else None,
                ttfb_ms=(first_line_at - start) * 1000 if first_line_at else None,
                first_sse_ms=(first_sse_at - start) * 1000 if first_sse_at else None,
                ttft_ms=(first_token_at - start) * 1000 if first_token_at else None,
                first_token_text=first_token_text,
                sse_events_before_token=sse_events,
                response_id=response_id,
                finish_reason=finish_reason,
            )
    except Exception as exc:
        return StreamTiming(
            ok=False,
            status_code=None,
            error=repr(exc),
            headers_ms=(headers_at - start) * 1000 if headers_at else None,
            ttfb_ms=None,
            first_sse_ms=None,
            ttft_ms=None,
            first_token_text=None,
            sse_events_before_token=sse_events,
            response_id=response_id,
            finish_reason=finish_reason,
        )


def summarize(label: str, rows: list[StreamTiming]) -> None:
    good = [r for r in rows if r.ok and r.ttft_ms is not None]
    failed = [r for r in rows if not r.ok]
    values = [r.ttft_ms for r in good if r.ttft_ms is not None]
    header_values = [r.headers_ms for r in good if r.headers_ms is not None]
    ttfb_values = [r.ttfb_ms for r in good if r.ttfb_ms is not None]
    sse_values = [r.first_sse_ms for r in good if r.first_sse_ms is not None]

    print()
    print(f"[summary] {label}")
    print(f"  success={len(good)} failed={len(failed)}")
    if not values:
        for row in failed:
            print(f"  error: {row.error}")
        return

    print(
        "  TTFT ms: "
        f"mean={statistics.mean(values):.2f} "
        f"median={statistics.median(values):.2f} "
        f"p90={pct(values, 0.90):.2f} "
        f"min={min(values):.2f} "
        f"max={max(values):.2f}"
    )
    if header_values:
        print(
            "  Headers ms: "
            f"mean={statistics.mean(header_values):.2f} "
            f"median={statistics.median(header_values):.2f}"
        )
    if ttfb_values:
        print(
            "  TTFB ms: "
            f"mean={statistics.mean(ttfb_values):.2f} "
            f"median={statistics.median(ttfb_values):.2f}"
        )
    if sse_values:
        print(
            "  First SSE ms: "
            f"mean={statistics.mean(sse_values):.2f} "
            f"median={statistics.median(sse_values):.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure streaming TTFT for an OpenAI-compatible server.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--targets", default="8192,16384,32768", help="Comma-separated target prompt token sizes.")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--tokens-per-filler",
        type=float,
        default=12.0,
        help="Approximate token count of one filler sentence. Adjust after checking printed prompt_tokens.",
    )
    parser.add_argument(
        "--cache-mode",
        choices=["same", "bust-prefix", "bust-suffix"],
        default="bust-prefix",
        help=(
            "same=reuse identical prompt; bust-prefix=random nonce at prompt start for cold-ish TTFT; "
            "bust-suffix=random nonce at end to observe long-prefix cache behavior."
        ),
    )
    parser.add_argument(
        "--count-tokens",
        choices=["tokenize", "nonstream", "none"],
        default="tokenize",
        help="Use /tokenize if available. nonstream can warm cache.",
    )
    parser.add_argument("--sleep", type=float, default=0.5, help="Sleep between rounds.")
    args = parser.parse_args()

    targets = [int(x.strip()) for x in args.targets.split(",") if x.strip()]
    base_url = args.base_url.rstrip("/")

    print("=" * 88)
    print("Streaming TTFT benchmark")
    print("=" * 88)
    print(f"base_url={base_url}")
    print(f"model={args.model}")
    print(f"targets={targets}")
    print(f"rounds={args.rounds} warmup={args.warmup} cache_mode={args.cache_mode}")
    print()

    check_server(base_url, args.api_key, args.timeout)
    print()

    for target in targets:
        label = f"target~{target}"
        print("=" * 88)
        print(f"[case] {label}")
        print("=" * 88)

        rows: list[StreamTiming] = []

        for i in range(args.warmup + args.rounds):
            is_warmup = i < args.warmup
            round_no = i + 1
            measured_no = i - args.warmup + 1

            prompt = make_prompt(target, args.tokens_per_filler, args.cache_mode)
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ]

            prompt_tokens: int | None = None
            if args.count_tokens == "tokenize":
                prompt_tokens = count_prompt_tokens_chat(
                    base_url, args.model, args.api_key, messages, args.timeout
                )
            elif args.count_tokens == "nonstream":
                prompt_tokens = count_prompt_tokens_nonstream(
                    base_url, args.model, args.api_key, messages, args.timeout
                )

            timing = parse_stream_for_first_token(
                base_url=base_url,
                model=args.model,
                api_key=args.api_key,
                messages=messages,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                timeout=args.timeout,
            )

            phase = "warmup" if is_warmup else f"round {measured_no}"
            token_part = f" prompt_tokens={prompt_tokens}" if prompt_tokens is not None else ""
            if timing.ok:
                print(
                    f"  {phase:<8}"
                    f"{token_part}"
                    f" headers={timing.headers_ms:.2f}ms"
                    f" ttfb={timing.ttfb_ms:.2f}ms"
                    f" first_sse={timing.first_sse_ms:.2f}ms"
                    f" ttft={timing.ttft_ms:.2f}ms"
                    f" events_before_token={timing.sse_events_before_token}"
                    f" first_token={timing.first_token_text!r}"
                )
            else:
                print(f"  {phase:<8}{token_part} ERROR {timing.error}")

            if not is_warmup:
                rows.append(timing)

            if args.sleep > 0:
                time.sleep(args.sleep)

        summarize(label, rows)

    print()
    print("=" * 88)
    print("Notes")
    print("=" * 88)
    print("TTFT here means request start -> first non-empty delta.content in the SSE stream.")
    print("Use --cache-mode=bust-prefix to reduce prefix-cache reuse between rounds.")
    print("Use --cache-mode=same to measure repeated identical prompt behavior.")
    print("Use --count-tokens=none if token counting endpoint is unavailable or affects your server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
