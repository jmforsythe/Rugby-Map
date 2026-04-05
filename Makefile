SEASON ?= 2025-2026

.PHONY: help install install-dev boundaries scrape addresses geocode distances maps pages webpages all scrape-fixtures match-day test lint clean

help:
	@echo "Usage: make <target> [SEASON=YYYY-YYYY]"
	@echo ""
	@echo "Targets:"
	@echo "  install      Install runtime dependencies"
	@echo "  install-dev  Install dev dependencies (linting, testing)"
	@echo "  boundaries   Download ONS boundary data"
	@echo "  scrape       Scrape league/team data from RFU"
	@echo "  addresses    Fetch team addresses from RFU"
	@echo "  geocode      Geocode team addresses"
	@echo "  distances    Calculate team travel distances"
	@echo "  maps         Generate interactive maps"
	@echo "  pages        Generate team pages"
	@echo "  webpages     Generate index pages"
	@echo "  all          Run the full pipeline (scrape -> maps)"
	@echo "  scrape-fixtures  Scrape fixtures from RFU (local only)"
	@echo "  match-day    Generate match-day map with date dropdown"
	@echo "  test         Run unit tests"
	@echo "  lint         Run linters"
	@echo "  clean        Remove generated output files"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

boundaries:
	python -m core.boundaries

scrape:
	python -m rugby.scrape --season $(SEASON)

addresses:
	python -m rugby.addresses --season $(SEASON)

geocode:
	python -m rugby.geocode --season $(SEASON)

distances:
	python -m rugby.distances --season $(SEASON)

maps:
	python -m rugby.maps --season $(SEASON)

pages:
	python -m rugby.team_pages

webpages:
	python -m rugby.webpages

all: scrape addresses geocode distances maps pages webpages

scrape-fixtures:
	python -m rugby.fixtures --season $(SEASON)

match-day:
	python -m rugby.match_day --season $(SEASON)

test:
	python -m pytest tests/ -v

lint:
	ruff check .
	black --check .
	isort --check .

clean:
	rm -rf dist/
