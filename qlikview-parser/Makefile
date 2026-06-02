PYTHON      ?= python3
ANTLR_JAR   := tools/antlr-4.13.1-complete.jar
GEN_DIR     := src/qlikview_parser/generated
GRAMMARS    := grammar/QlikViewLexer.g4 grammar/QlikViewParser.g4

.PHONY: grammar test clean install

grammar:
	@mkdir -p $(GEN_DIR)
	# Lexer first so the parser can read its .tokens file.
	cd grammar && java -jar ../$(ANTLR_JAR) -Dlanguage=Python3 \
	    QlikViewLexer.g4
	cd grammar && java -jar ../$(ANTLR_JAR) -Dlanguage=Python3 \
	    -visitor -listener QlikViewParser.g4
	mv grammar/QlikView*.py $(GEN_DIR)/
	mv grammar/QlikView*.tokens $(GEN_DIR)/
	mv grammar/QlikView*.interp $(GEN_DIR)/ 2>/dev/null || true
	@touch $(GEN_DIR)/__init__.py

install:
	$(PYTHON) -m pip install -e .

test: grammar
	$(PYTHON) -m pytest tests/ -m "not neo4j" -v

clean:
	rm -rf $(GEN_DIR)/*.py $(GEN_DIR)/*.tokens $(GEN_DIR)/*.interp
	@touch $(GEN_DIR)/__init__.py
