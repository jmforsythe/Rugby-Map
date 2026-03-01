SEASON ?= 2025-2026

.PHONY: help install install-dev boundaries scrape addresses geocode distances maps pages webpages all test lint clean

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
	@echo "  test         Run unit tests"
	@echo "  lint         Run linters"
	@echo "  clean        Remove generated output files"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

boundaries:
	python download_boundaries.py

scrape:
	python scrape_leagues_teams.py --season $(SEASON)

addresses:
	python fetch_addresses.py --season $(SEASON)

geocode:
	python geocode_addresses.py --season $(SEASON)

distances:
	python calculate_team_distances.py --season $(SEASON)

maps:
	python make_tier_maps.py --season $(SEASON)

pages:
	python team_pages.py

webpages:
	python generate_webpages.py

all: scrape addresses geocode distances maps pages webpages

test:
	python -m pytest tests/ -v

lint:
	ruff check .
	black --check .
	isort --check .

clean:
	rm -rf tier_maps/
