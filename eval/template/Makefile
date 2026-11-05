.PHONY: test lint gen gen-check

test:
	SHOP_ENV=test python3 -m unittest discover -s tests -q

lint:
	python3 tools/lint.py

gen:
	python3 tools/gen_schema.py

gen-check:
	python3 tools/gen_schema.py --check
