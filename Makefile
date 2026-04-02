# Makefile for managing the translations of ckanext-downloadall

.PHONY: help extract update compile

help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  extract        to extract all messages"
	@echo "  update         to update the catalog for German"
	@echo "  compile        to compile the catalog for German"

extract:
	python3 setup.py extract_messages

update:
	python3 setup.py update_catalog --locale de
	python3 setup.py update_catalog --locale fr

compile:
	python3 setup.py compile_catalog --locale de
	python3 setup.py compile_catalog --locale fr
