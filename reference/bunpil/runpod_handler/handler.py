"""Qwen2.5-14B-Instruct-AWQ — tool calling 지원."""
import sys
import os
import uuid
import re
import json
import traceback
import subprocess

print("=== handler.py v9 시작 ===", flush=True)
print(f"Python: {sys.version}", flush=True)

try:
    r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                        "--format=csv,noheader"],
                       capture_output=True, text=True, timeout=10)
    print(f"nvidia-smi stdout: {r.stdout.strip()}", flush=True)
    print(f"nvidia-smi stderr: {r.stderr.strip()}", flush=True)
except Exception as e:
    print(f"nvidia-smi 실행 실패: {e}", flush=True)

print(f"CUDA_VISIBLE_DEVICES: {os.environ.get('CUDA_VISIBLE_DEVICES', 'N/A')}", flush=True)

try:
    import runpod
    print("runpod import OK", flush=True)
except Exception as e:
    print(f"runpod import 실패: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

try:
    import torch
    print(f"torch: {torch.__version__}", flush=True)
    print(f"CUDA available: {torch.cuda.is_available()}", flush=True)
    if torch.cuda.is_available():
        print(f"CUDA device: {torch.cuda.get_device_name(0)}", flush=True)
        print(f"VRAM: {torch.cuda.get_device_properties(0).total_memory // 1024**3} GB", flush=True)
except Exception as e:
    print(f"torch CUDA 확인 실패: {e}", flush=True)
    traceback.print_exc()

try:
    print("vllm import 시도...", flush=True)
    from vllm import LLM, SamplingParams
    print("vllm import OK", flush=True)
except Exception as e:
    print(f"vllm import 실패: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)

MODEL = "Qwen/Qwen2.5-14B-Instruct-AWQ"  # RTX A5000 24GB에 float16 14B가 안 들어가 AWQ 4bit 사용
print(f"모델 로드 시작: {MODEL}", flush=True)
try:
    llm = LLM(model=MODEL, quantization="awq", dtype="float16", gpu_memory_utilization=0.90)
    tokenizer = llm.get_tokenizer()
    print("모델 로드 완료", flush=True)
except Exception as e:
    print(f"모델 로드 실패: {e}", flush=True)
    traceback.print_exc()
    sys.exit(1)


def _build_prompt(messages: list) -> str:
    """tools 없는 경우 수동 포맷 (기존 방식)."""
    prompt = ""
    for msg in messages:
        role    = msg.get("role", "user")
        content = msg.get("content") or ""
        prompt += f"<|im_start|>{role}\n{content}<|im_end|>\n"
    prompt += "<|im_start|>assistant\n"
    return prompt


def _parse_tool_calls(text: str):
    """Qwen tool call 태그 파싱 → OpenAI 호환 구조체 반환."""
    matches = re.findall(r'<tool_call>\s*(.*?)\s*</tool_call>', text, re.DOTALL)
    if not matches:
        return None
    result = []
    for m in matches:
        try:
            data = json.loads(m)
            args = data.get("arguments", {})
            # Qwen이 arguments를 dict 또는 JSON 문자열로 출력할 수 있음
            arguments_str = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            result.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": data["name"],
                    "arguments": arguments_str,
                },
            })
        except Exception:
            pass
    return result or None


def handler(job: dict) -> dict:
    inp         = job.get("input", {})
    messages    = inp.get("messages", [])
    max_tokens  = int(inp.get("max_tokens", 512))
    temperature = float(inp.get("temperature", 0.7))
    tools       = inp.get("tools", None)
    stop        = inp.get("stop", ["<|im_end|>"])

    if not messages:
        return {"error": "messages 필드가 비어 있습니다."}

    if tools:
        try:
            prompt = tokenizer.apply_chat_template(
                messages,
                tools=tools,
                tokenize=False,
                add_generation_prompt=True,
            )
        except Exception as e:
            print(f"apply_chat_template 실패, 폴백: {e}", flush=True)
            prompt = _build_prompt(messages)
    else:
        prompt = _build_prompt(messages)

    params  = SamplingParams(temperature=temperature, max_tokens=max_tokens, stop=stop)
    outputs = llm.generate([prompt], params)
    text    = outputs[0].outputs[0].text.strip()

    if tools:
        tool_calls = _parse_tool_calls(text)
        if tool_calls:
            return {"response": None, "tool_calls": tool_calls}

    return {"response": text, "tool_calls": None}


runpod.serverless.start({"handler": handler})
