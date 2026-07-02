"""خدمات تشغيل MLX المحلي مع routing حسب المسار."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings
from app.mode_runtime_guard import (
    build_completion_repair_user_prompt,
    build_repair_user_prompt,
    choose_best_candidate,
    sanitize_output,
    should_attempt_completion_repair,
    should_attempt_repair,
)

logger = logging.getLogger(__name__)
settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent

APP_TO_MLX_MODE = {
    "consultation": "legal_opinion",
    "legal_memo": "legal_memo",
    "legal_analysis": "legal_analysis",
}
PROMPT_TEMPLATE_BY_MODE = {
    "legal_opinion": "legal_opinion.system.txt",
    "legal_memo": "legal_memo.system.txt",
    "legal_analysis": "legal_analysis.system.txt",
}
THOUGHT_BLOCK_RE = re.compile(r"^\s*<\|channel>thought.*?(?:<channel\|>)", re.DOTALL)
THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
USER_PROMPT_SPLIT_MARKER = "\n\nالنصوص المسترجعة:\n"


def _resolve_project_path(path_value: str) -> Path:
    configured = Path(path_value)
    if configured.is_absolute():
        return configured
    return (PROJECT_ROOT / configured).resolve()


def normalize_mlx_mode(answer_mode: Optional[str]) -> str:
    normalized = str(answer_mode or "").strip().lower()
    return APP_TO_MLX_MODE.get(normalized, "legal_opinion")


@lru_cache(maxsize=16)
def _load_json_payload(path_value: str) -> dict[str, Any]:
    path = _resolve_project_path(path_value)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("تعذر قراءة ملف JSON الخاص بـ MLX من %s: %s", path, exc)
        return {}


@lru_cache(maxsize=16)
def _load_prompt_template(prompt_dir_value: str, mode_name: str) -> str:
    prompt_name = PROMPT_TEMPLATE_BY_MODE.get(mode_name)
    if not prompt_name:
        raise ValueError(f"نمط MLX غير مدعوم: {mode_name}")
    prompt_dir = _resolve_project_path(prompt_dir_value)
    prompt_path = prompt_dir / prompt_name
    return prompt_path.read_text(encoding="utf-8").strip()


def resolve_mlx_local_prompt(answer_mode: Optional[str]) -> str:
    return _load_prompt_template(settings.mlx_local_prompt_templates_dir, normalize_mlx_mode(answer_mode))


def build_mlx_local_user_prompt(question: str, context: str) -> str:
    return f"القضية:\n{question}\n\nالنصوص المسترجعة:\n{context}"


def split_mlx_local_user_prompt(user_message: str) -> tuple[str, str]:
    text = str(user_message or "")
    if text.startswith("القضية:\n"):
        body = text[len("القضية:\n"):]
        if USER_PROMPT_SPLIT_MARKER in body:
            question, context = body.split(USER_PROMPT_SPLIT_MARKER, 1)
            return question.strip(), context.strip()
    return "", text.strip()


def _resolve_mode_adapter(runtime: dict[str, Any], answer_mode: Optional[str]) -> str:
    explicit_adapter = str(runtime.get("adapter_path") or "").strip()
    if explicit_adapter:
        return explicit_adapter

    routing_path_value = str(
        runtime.get("routing_policy_path")
        or settings.mlx_local_routing_policy_path
    ).strip()
    routing_payload = _load_json_payload(routing_path_value)
    mode_name = normalize_mlx_mode(answer_mode)
    mode_route = routing_payload.get("routing", {}).get(mode_name, {})
    fallback_route = routing_payload.get("fallback", {})
    candidate = (
        mode_route.get("adapter")
        or fallback_route.get("adapter")
        or settings.mlx_local_default_adapter_path
    )
    return str(candidate or "").strip()


def resolve_mlx_local_max_tokens(
    runtime: dict[str, Any],
    answer_mode: Optional[str],
    requested_max_tokens: Optional[int] = None,
) -> int:
    default_max_tokens = requested_max_tokens or int(
        runtime.get("max_tokens") or settings.generation_max_tokens
    )
    policy_path_value = str(
        runtime.get("budget_policy_path")
        or settings.mlx_local_budget_policy_path
    ).strip()
    policy_payload = _load_json_payload(policy_path_value)
    mode_name = normalize_mlx_mode(answer_mode)
    mode_policy = policy_payload.get("policies", {}).get(mode_name, {})
    try:
        policy_max_tokens = int(mode_policy.get("max_tokens", default_max_tokens))
    except (TypeError, ValueError):
        policy_max_tokens = default_max_tokens

    # نرفع الحد الأدنى تلقائياً للمسارات الطويلة حتى لا نعيد خطأ truncation القديم.
    return max(128, min(max(default_max_tokens, policy_max_tokens), 8192))


def strip_mlx_thought_artifacts(text: str) -> str:
    cleaned = str(text or "").replace("\r\n", "\n").strip()
    cleaned = THINK_TAG_RE.sub("", cleaned)
    cleaned = THOUGHT_BLOCK_RE.sub("", cleaned).strip()
    if "<channel|>" in cleaned:
        cleaned = cleaned.split("<channel|>", 1)[1].strip()
    cleaned = cleaned.replace("<channel|>", "").strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def should_apply_runtime_guard(answer_mode: Optional[str]) -> bool:
    return normalize_mlx_mode(answer_mode) == "legal_memo"


def build_mlx_local_healthcheck(provider_state: dict[str, Any]) -> dict[str, Any]:
    python_bin = _resolve_project_path(
        str(provider_state.get("python_bin") or settings.mlx_local_python_bin)
    )
    model_path = _resolve_project_path(
        str(provider_state.get("model") or settings.mlx_local_model_path)
    )
    routing_path = _resolve_project_path(
        str(provider_state.get("routing_policy_path") or settings.mlx_local_routing_policy_path)
    )
    prompt_dir = _resolve_project_path(
        str(provider_state.get("prompt_templates_dir") or settings.mlx_local_prompt_templates_dir)
    )
    default_adapter = _resolve_project_path(
        str(provider_state.get("default_adapter_path") or settings.mlx_local_default_adapter_path)
    )

    checks = [
        ("Python", python_bin.exists()),
        ("Base model", model_path.exists()),
        ("Routing", routing_path.exists()),
        ("Prompts", prompt_dir.exists()),
        ("Fallback adapter", default_adapter.exists()),
    ]
    available = sum(1 for _, ok in checks if ok)
    message = " | ".join(
        f"{label}: {'جاهز' if ok else 'مفقود'}"
        for label, ok in checks
    )
    return {
        "configured": python_bin.exists() and model_path.exists(),
        "connection_ok": available >= 4,
        "connection_message": message,
        "connection_target": str(python_bin),
        "catalog_message": f"ملف routing: {routing_path} | fallback adapter: {default_adapter}",
        "selected_model_available": model_path.exists(),
        "resolved_model": str(model_path),
        "model_suggestions": [
            str(model_path),
        ],
    }


async def generate_with_mlx_local(
    runtime: dict[str, Any],
    *,
    system_prompt: str,
    user_message: str,
    answer_mode: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    python_bin = _resolve_project_path(
        str(runtime.get("python_bin") or settings.mlx_local_python_bin)
    )
    runner_script = _resolve_project_path(settings.mlx_local_runner_script_path)
    model_path = _resolve_project_path(
        str(runtime.get("model") or settings.mlx_local_model_path)
    )
    adapter_path = _resolve_mode_adapter(runtime, answer_mode)
    resolved_max_tokens = resolve_mlx_local_max_tokens(runtime, answer_mode, max_tokens)
    resolved_temperature = (
        float(runtime.get("temperature", settings.generation_temperature))
        if temperature is None
        else float(temperature)
    )

    if not python_bin.exists():
        raise ValueError(f"ملف Python الخاص بـ MLX غير موجود: {python_bin}")
    if not runner_script.exists():
        raise ValueError(f"سكريبت تشغيل MLX غير موجود: {runner_script}")
    if not model_path.exists():
        raise ValueError(f"مسار النموذج المحلي غير موجود: {model_path}")

    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
    }
    async def invoke_local_generation(
        *,
        messages: list[dict[str, str]],
        call_temperature: float,
        call_max_tokens: int,
    ) -> str:
        payload = {"messages": messages}
        local_command = [
            str(python_bin),
            str(runner_script),
            "--model",
            str(model_path),
            "--temperature",
            str(call_temperature),
            "--max-tokens",
            str(call_max_tokens),
        ]
        if adapter_path:
            local_command.extend(["--adapter-path", adapter_path])

        process = await asyncio.create_subprocess_exec(
            *local_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            timeout=float(settings.mlx_local_timeout_seconds),
        )

        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(stderr_text or "فشل تشغيل MLX المحلي بدون رسالة واضحة.")

        raw_payload = stdout.decode("utf-8", errors="replace").strip()
        try:
            response_payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"خرج MLX المحلي بتنسيق غير متوقع: {exc}") from exc

        text = strip_mlx_thought_artifacts(response_payload.get("text", ""))
        if not text:
            raise ValueError("MLX المحلي لم يرجع نصاً قابلاً للعرض.")
        return text
    text = await invoke_local_generation(
        messages=payload["messages"],
        call_temperature=resolved_temperature,
        call_max_tokens=resolved_max_tokens,
    )
    mode_name = normalize_mlx_mode(answer_mode)
    if not should_apply_runtime_guard(answer_mode):
        return text

    question, context = split_mlx_local_user_prompt(user_message)
    guarded_text, initial_report = sanitize_output(mode_name, text)
    selected_name = "initial_guarded"
    selected_text = guarded_text
    selected_report = initial_report

    logger.info(
        "🛡️ memo runtime guard: initial coverage=%s missing=%s over_limit=%s",
        initial_report.get("section_coverage"),
        len(initial_report.get("missing_sections", [])),
        initial_report.get("over_limit_chars"),
    )

    repair_temperature = min(resolved_temperature, 0.1)
    repair_max_tokens = resolved_max_tokens

    if should_attempt_repair(initial_report) and question and context:
        repair_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": build_repair_user_prompt(
                    mode=mode_name,
                    question=question,
                    context=context,
                    draft=guarded_text,
                    report=initial_report,
                ),
            },
        ]
        try:
            repaired_text = await invoke_local_generation(
                messages=repair_messages,
                call_temperature=repair_temperature,
                call_max_tokens=repair_max_tokens,
            )
            repaired_text, repair_report = sanitize_output(mode_name, repaired_text)
            selected = choose_best_candidate(
                mode_name,
                [
                    {"name": "initial_guarded", "text": guarded_text, "report": initial_report},
                    {"name": "repair_guarded", "text": repaired_text, "report": repair_report},
                ],
            )
            selected_name = selected["name"]
            selected_text = selected["text"]
            selected_report = selected["report"]
        except Exception as exc:
            logger.warning("تعذر تنفيذ memo repair pass في MLX المحلي: %s", exc)

    if should_attempt_completion_repair(selected_report) and question and context:
        completion_messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": build_completion_repair_user_prompt(
                    mode=mode_name,
                    question=question,
                    context=context,
                    draft=selected_text,
                    report=selected_report,
                ),
            },
        ]
        try:
            completion_text = await invoke_local_generation(
                messages=completion_messages,
                call_temperature=repair_temperature,
                call_max_tokens=repair_max_tokens,
            )
            completion_text, completion_report = sanitize_output(mode_name, completion_text)
            selected = choose_best_candidate(
                mode_name,
                [
                    {"name": selected_name, "text": selected_text, "report": selected_report},
                    {"name": "completion_guarded", "text": completion_text, "report": completion_report},
                ],
            )
            selected_name = selected["name"]
            selected_text = selected["text"]
            selected_report = selected["report"]
        except Exception as exc:
            logger.warning("تعذر تنفيذ memo completion repair في MLX المحلي: %s", exc)

    logger.info(
        "🛡️ memo runtime guard selected=%s coverage=%s missing=%s",
        selected_name,
        selected_report.get("section_coverage"),
        len(selected_report.get("missing_sections", [])),
    )
    return selected_text
