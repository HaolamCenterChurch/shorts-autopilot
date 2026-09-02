#!/usr/bin/env python3
"""AI 백엔드 어댑터 — BYO-CLI.

'프롬프트와 컨텍스트를 받아 텍스트를 돌려주는 외부 명령' 인터페이스.
사용자의 LLM CLI (claude -p, gemini -p, agy -p, codex exec 등)를 지원합니다.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid

from app.paths import find_binary, augmented_path_str

# 앱 지원 디렉토리
_SUPPORT = os.path.expanduser("~/.config/shorts-autopilot")
os.makedirs(_SUPPORT, exist_ok=True)
CONFIG_PATH = os.path.join(_SUPPORT, "config.json")
DEFAULT_TIMEOUT = 240

KNOWN_BACKENDS = [
    {"key": "claude", "label": "Claude Code (claude -p)", "bin": "claude", "cmd": ["claude", "-p"]},
    {"key": "gemini", "label": "Gemini CLI (gemini -p)", "bin": "gemini", "cmd": ["gemini", "-p"]},
    {"key": "antigravity", "label": "Antigravity (agy -p)", "bin": "agy", "cmd": ["agy", "-p"]},
    {"key": "codex", "label": "Codex CLI (codex exec)", "bin": "codex", "cmd": ["codex", "exec"]},
]

def list_backends() -> dict:
    backends = []
    for b in KNOWN_BACKENDS:
        backends.append({
            **{k: b[k] for k in ("key", "label", "cmd")},
            "available": find_binary(b["bin"]) is not None
        })
    return {"backends": backends, "current": load_config().get("ai_cmd")}

def set_ai_cmd(cmd):
    if isinstance(cmd, str):
        cmd = cmd.split()
    cfg = load_config()
    cfg["ai_cmd"] = cmd
    save_config(cfg)
    return cmd

def detect_default_cmd():
    for b in KNOWN_BACKENDS:
        if find_binary(b["bin"]):
            return list(b["cmd"])
    return ["gemini", "-p"]

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    cfg = {"ai_cmd": detect_default_cmd()}
    save_config(cfg)
    return cfg

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

ARG_LIMIT = 400_000
CANARY_KEY = "입력확인"

def _compose(prompt: str, context: str) -> str:
    if not context:
        return prompt
    return (
        prompt
        + "\n\n" + "=" * 60
        + "\n# 여기부터가 이번에 처리할 **실제 입력**이다."
        + "\n# 위 지시문 안의 예시는 형식 견본일 뿐이다. 반드시 아래 입력만 근거로 답하라."
        + "\n" + "=" * 60 + "\n" + context
    )

def _aug_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = augmented_path_str()
    return env

def run_ai(prompt: str, context: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    cfg = load_config()
    cmd = list(cfg.get("ai_cmd") or detect_default_cmd())
    cmd[0] = find_binary(cmd[0]) or cmd[0]

    with tempfile.TemporaryDirectory(prefix="shorts_ai_") as workdir:
        full = _compose(prompt, context)
        if len(full.encode("utf-8")) > ARG_LIMIT:
            ctx_path = os.path.join(workdir, "transcript.txt")
            with open(ctx_path, "w", encoding="utf-8") as f:
                f.write(context)
            full = _compose(
                prompt,
                "(입력이 길어 파일로 두었다. 이 파일을 끝까지 읽고 답하라.)\n" + ctx_path,
            )
        proc = subprocess.run(
            cmd + [full],
            input=context,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_aug_env(),
            timeout=timeout,
            cwd=workdir,
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"AI CLI 실패 (cmd={cmd}, code={proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
        )
    return proc.stdout

def parse_json(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = m.group(1) if m else None
    if candidate is None:
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        break
    if candidate is None:
        raise ValueError(f"AI 응답에서 JSON을 찾지 못함: {text[:300]}")
    return json.loads(candidate)

def run_ai_json(prompt: str, context: str, timeout: int = DEFAULT_TIMEOUT,
                verify_input: bool = True) -> dict:
    """AI 호출 + JSON 파싱 및 카나리 입력 검증."""
    if not verify_input or not context:
        return parse_json(run_ai(prompt, context, timeout))

    code = uuid.uuid4().hex[:8].upper()
    ctx = f"입력확인코드: {code}\n{context}"
    p = prompt + (
        f'\n\n## 입력 확인 (필수)\n'
        f'출력 JSON 최상위에 `"{CANARY_KEY}"` 키를 넣고, 값으로 **입력 맨 첫 줄의 '
        f'`입력확인코드`** 를 그대로 적어라. 입력을 읽지 못했다면 빈 문자열을 넣어라.'
    )
    data = parse_json(run_ai(p, ctx, timeout))
    got = str(data.get(CANARY_KEY, "")).strip().upper()
    if got != code:
        raise RuntimeError(
            "AI가 현재 입력 데이터를 읽지 않고 응답했습니다 (카나리 검증 실패). "
            f"기대값={code}, 반환값={got or '없음'}."
        )
    data.pop(CANARY_KEY, None)
    return data
