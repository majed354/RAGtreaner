"""تشغيل توليد MLX محلي لرسالة واحدة عبر stdin/stdout."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--adapter-path", default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=1200)
    args = parser.parse_args()

    payload = json.load(sys.stdin)
    messages = payload.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise SystemExit("يجب تمرير messages صالحة عبر stdin.")

    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = load(args.model, adapter_path=args.adapter_path)
    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    sampler = make_sampler(temp=args.temperature)
    text = generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=args.max_tokens,
        sampler=sampler,
        verbose=False,
    ).strip()
    json.dump({"text": text}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
