create a virtual env


uv venv --python 3.11


uv pip install -r requirements.txt

.venv\Scripts\activate

uv run pytest -q

uv run pytest
tests/test_seeding.py -q

uv run pytest 
tests/test_sim.py -q

uv run pytest 
tests/test_fork.py -q -s

uv run python 
scripts/lab1_network_demo.py