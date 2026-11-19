PY := .venv/bin/python
LOCAL_ENV := AWS_REGION=ap-southeast-2 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
	DYNAMODB_ENDPOINT=http://localhost:4566 GATEWAY_DEMO_API_KEY=sk-gw-local-demo-key \
	MOCK_MODE=$${MOCK_MODE:-true}

.PHONY: install test up down localstack run lint

install:
	python3 -m venv .venv && $(PY) -m pip install -q -r requirements-dev.txt

test:
	$(PY) -m pytest

up:
	docker compose up --build

down:
	docker compose down -v

# if you don't have docker compose: start localstack with docker, run the gateway from the venv
localstack:
	docker run -d --rm --name llmgw-localstack -p 4566:4566 -e SERVICES=dynamodb localstack/localstack:4.4

run:
	$(LOCAL_ENV) $(PY) scripts/bootstrap_local.py && $(LOCAL_ENV) $(PY) scripts/local_server.py

lint:
	uvx cfn-lint template.yaml
