## create a virtual env as:

```
uv venv --python 3.11
```


```
uv pip install -r requirements.txt
```
```
.venv\Scripts\activate
```

```
uv run pytest -q
```

```
uv run pytest
tests/test_seeding.py -q
```

```
uv run pytest 
tests/test_sim.py -q
```

```
uv run pytest 
tests/test_fork.py -q -s
```

```
uv run python 
scripts/lab1_network_demo.py
```

To test frontends:

## Demo 1:
```
streamlit run demo/review1_app.py
```


```
python scripts/run_experiments.py --arms A0,A1,A2,A3,A4 --seeds 0,1 --provider cloud
```


How to run each path now
Offline, no model needed (this is what feeds Level 12 — fully deterministic, resumable):


python scripts/run_experiments.py                      # full 270-run scripted sweep
python scripts/run_experiments.py --arms A0,A1 --seeds 0,1   # quick smoke
Local Ollama (start ollama serve first, pull a model):


python scripts/run_experiments.py --provider local --model qwen2.5:7b-instruct
(default base-url already points at Ollama's localhost:11434/v1.)

Cloud (OpenAI-compatible) — set the key, pass the endpoint + model:


$env:LLM_API_KEY = "your-key"
# Gemini (OpenAI-compat):
python scripts/run_experiments.py --provider cloud `
  --base-url https://generativelanguage.googleapis.com/v1beta/openai `
  --model gemini-2.5-flash
# OpenAI:
python scripts/run_experiments.py --provider cloud `
  --base-url https://api.openai.com/v1 --model gpt-4o-mini