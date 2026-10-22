import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    __version__ as transformers_version,
)


parser = argparse.ArgumentParser()
parser.add_argument("prompt_file", type=Path)
parser.add_argument("--seed", type=int, default=42)
args = parser.parse_args()

prompt = args.prompt_file.read_text(encoding="utf-8")

if not torch.cuda.is_available():
    raise SystemExit("GPU недоступна для PyTorch")

model_id = "Qwen/Qwen3-1.7B"
revision = "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
seed = args.seed
generation = {
    "max_new_tokens": 768,
    "do_sample": True,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
}

run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
output_dir = Path("results/raw") / f"diagnosis-{run_id}"
output_dir.mkdir(parents=True, exist_ok=False)
(output_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

print("Загрузка модели из кэша...", flush=True)
tokenizer = AutoTokenizer.from_pretrained(
    model_id, revision=revision, local_files_only=True
)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    revision=revision,
    local_files_only=True,
    dtype=torch.bfloat16,
    device_map={"": 0},
    low_cpu_mem_usage=True,
)
model.eval()

formatted = tokenizer.apply_chat_template(
    [{"role": "user", "content": prompt}],
    tokenize=False,
    add_generation_prompt=True,
    enable_thinking=False,
)
(output_dir / "formatted_prompt.txt").write_text(
    formatted, encoding="utf-8"
)
inputs = tokenizer(formatted, return_tensors="pt").to("cuda")

torch.manual_seed(seed)
torch.cuda.synchronize()
started = time.perf_counter()

with torch.inference_mode():
    output = model.generate(**inputs, **generation)

torch.cuda.synchronize()
elapsed = time.perf_counter() - started

answer_tokens = output[0, inputs["input_ids"].shape[1]:]
answer = tokenizer.decode(answer_tokens, skip_special_tokens=True)

metadata = {
    "prompt_file": args.prompt_file.as_posix(),
    "model": model_id,
    "revision": revision,
    "dtype": "bfloat16",
    "enable_thinking": False,
    "seed": seed,
    "generation": generation,
    "torch_version": torch.__version__,
    "transformers_version": transformers_version,
    "gpu": torch.cuda.get_device_name(0),
    "input_tokens": inputs["input_ids"].shape[1],
    "output_tokens": len(answer_tokens),
    "generation_seconds": round(elapsed, 3),
    "token_limit_reached": (
        len(answer_tokens) >= generation["max_new_tokens"]
    ),
}

(output_dir / "answer.txt").write_text(answer, encoding="utf-8")
(output_dir / "metadata.json").write_text(
    json.dumps(metadata, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("\n--- Ответ модели ---")
print(answer)
print("\nРезультаты сохранены:", output_dir)
print("Достигнут лимит ответа:", metadata["token_limit_reached"])
