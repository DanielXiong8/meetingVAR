"""meetingVAR 本地语义引擎：transformers 加载 Qwen3，暴露 OpenAI 兼容 /v1/chat/completions。

为什么存在：LLM 语义层（F1 词表/厚纪要）要"数据不出机"，云端 API 是可选项；
本服务让 MEETINGVAR_LLM_BASE_URL=http://127.0.0.1:11438/v1 即可全离线。

用法：
    python serve_local_llm.py [--model D:/env/models/models/Qwen--...-FP8/snapshots/master] [--port 11438]

设计：单进程串行推理（抽提/digest 都是低频调用）；Chat Template 用模型自带 tokenizer。
"""
from __future__ import annotations

import argparse
import torch
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL_DIR_CANDIDATES = [
    "D:/env/models/qwen3-4b-bf16",
    "D:/env/models/models/Qwen--Qwen3-4B-Instruct-2507-FP8/snapshots/master",
    "D:/env/models/models/Qwen--Qwen3-4B-Instruct-2507/snapshots/master",
    "D:/env/models/models/Qwen--Qwen3-4B/snapshots/master",
]

_state = {"model": None, "tok": None, "lock": threading.Lock()}


def ensure_model(model_dir: str) -> None:
    if _state["model"] is not None:
        return
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"[llm] loading {model_dir} ...", flush=True)
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(model_dir)
    # Windows 无 triton：FP8 checkpoints 的 finegrained kernel 跑不了。
    # 用 dequant 路线：bfloat16 装载（权重 4B≈8GB，RTX 5060 8GB 恰好放得下，
    # 留给 KV-cache 的空间靠 max_new_tokens 限制兜底）。
    model = AutoModelForCausalLM.from_pretrained(
        model_dir,
        dtype=torch.bfloat16,
        device_map="auto", max_memory={0: "6.5GiB", "cpu": "24GiB"}, offload_folder="D:/env/llm_offload",
    )
    model.eval()
    _state["model"], _state["tok"] = model, tok
    print(f"[llm] loaded in {time.time()-t0:.0f}s", flush=True)


def chat(messages: list[dict], temperature: float = 0.3, max_new_tokens: int = 768) -> str:
    with _state["lock"]:
        ensure_model(_state.get("model_dir", MODEL_DIR_CANDIDATES[0]))
        tok, model = _state["tok"], _state["model"]
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tok(prompt, return_tensors="pt").to(model.device)
        import torch

        with torch.inference_mode():
            out = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=0.9,
                do_sample=temperature > 0,
            )
        text = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        return text.strip()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        if not self.path.endswith("/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = req.get("messages", [])
            temperature = float(req.get("temperature", 0.3))
            max_new = int(req.get("max_tokens", 1024))
            t0 = time.time()
            content = chat(messages, temperature, max_new)
            resp = {
                "id": "local",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "local-qwen3",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"latency_s": round(time.time() - t0, 1)},
            }
            body = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:  # noqa: BLE001
            body = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--port", type=int, default=11438)
    args = ap.parse_args()

    model_dir = args.model or next((c for c in MODEL_DIR_CANDIDATES if Path(c).exists()), None)
    if not model_dir:
        raise SystemExit("未找到模型目录；用 --model 指定")
    _state["model_dir"] = model_dir

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[llm] serving /v1/chat/completions on 127.0.0.1:{args.port} (model loads on first call)", flush=True)

    # 预热：起服务即加载模型，避免第一个请求等 2 分钟
    threading.Thread(target=lambda: ensure_model(model_dir), daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
