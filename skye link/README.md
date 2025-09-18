## Run
python -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install .
uvicorn src.main:app --reload

## Test
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Say hello"}'