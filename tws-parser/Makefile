ANTLR_VERSION := 4.13.1
ANTLR_JAR := tools/antlr-$(ANTLR_VERSION)-complete.jar
ANTLR_URL := https://www.antlr.org/download/antlr-$(ANTLR_VERSION)-complete.jar

GRAMMAR_SRC := grammar/TWSComposerLexer.g4 grammar/TWSComposerParser.g4
GEN_DIR := src/tws_parser/generated
GEN_SENTINEL := $(GEN_DIR)/TWSComposerParser.py

PYTHON ?= .venv/bin/python

.PHONY: all grammar test run clean help

help:
	@echo "Targets:"
	@echo "  grammar       Regenerate ANTLR Python sources from grammar/*.g4"
	@echo "  test          Run pytest (depends on grammar)"
	@echo "  migrate       Apply Alembic migrations against POSTGRES_HOST"
	@echo "  run           uvicorn dev server on :8002"
	@echo "  clean         Remove generated/, build/, dist/, caches"

$(ANTLR_JAR):
	@mkdir -p tools
	@echo "Downloading ANTLR $(ANTLR_VERSION) ..."
	curl -fsSL -o $(ANTLR_JAR) $(ANTLR_URL)

grammar: $(GEN_SENTINEL)

$(GEN_SENTINEL): $(GRAMMAR_SRC) $(ANTLR_JAR)
	@mkdir -p $(GEN_DIR)
	@# Run from inside grammar/ so the parser's `tokenVocab = TWSComposerLexer`
	@# resolves against the lexer's freshly-generated .tokens file.
	cd grammar && java -jar ../$(ANTLR_JAR) \
	    -Dlanguage=Python3 -visitor -listener -no-listener \
	    -o ../$(GEN_DIR) \
	    TWSComposerLexer.g4 TWSComposerParser.g4
	@touch $(GEN_DIR)/__init__.py

test: grammar
	$(PYTHON) -m pytest tests/ -v

migrate:
	$(PYTHON) -m alembic upgrade head

run: grammar
	$(PYTHON) -m uvicorn tws_parser.main:app --reload --port 8002

clean:
	rm -rf build/ dist/ .pytest_cache .ruff_cache htmlcov/ .coverage
	find $(GEN_DIR) -type f \( -name '*.py' -o -name '*.interp' -o -name '*.tokens' \) ! -name '__init__.py' -delete
