.PHONY: check test test-scripts public-check

VERBOTE ?=
MERKZETTEL ?=

check:
	python3 scripts/check_numbers.py --require-paper

test:
	PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tools/tests -q -p no:cacheprovider

test-scripts:
	PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/tests -q -p no:cacheprovider

public-check:
	python3 scripts/check_public.py --verbote $(VERBOTE) --merkzettel $(MERKZETTEL)
