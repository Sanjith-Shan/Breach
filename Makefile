.PHONY: help install sanity test selftest gen build report clean

help:
	@echo "make install   install runtime + dev dependencies"
	@echo "make sanity    static + synthetic checks over the suite (offline)"
	@echo "make test      harness unit tests and suite integrity (offline)"
	@echo "make selftest  one offline trial per task with the attacker/noop stand-ins"
	@echo "make gen       regenerate the task suite under tasks/"
	@echo "make build     build the sandbox image (needs Docker)"
	@echo "make report    rebuild the tables from the last results file"

install:
	pip install -r requirements.txt -r requirements-dev.txt

sanity:
	python3 -m breach sanity

test:
	python3 -m pytest -q

selftest:
	python3 -m breach --out /tmp/breach-selftest --workroot /tmp/breach-selftest-work \
		run --agents attacker noop --conditions baseline --trials 1

gen:
	python3 scripts/gen_suite.py

build:
	python3 -m breach build

report:
	python3 -m breach report results/latest/results.jsonl

clean:
	rm -rf /tmp/breach-work /tmp/breach-selftest /tmp/breach-selftest-work results/tmp
