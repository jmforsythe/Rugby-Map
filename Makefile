SEASON ?= 2025-2026
# Set FORCE=1 to re-scrape / re-address / re-geocode even when output files exist
FORCE_FLAG := $(if $(filter 1,$(FORCE)),--force,)

.PHONY: help install install-dev boundaries scrape addresses geocode distances routed-distances maps pages webpages custom-map-data all scrape-fixtures match-day review-screenshots test lint clean

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
	@echo "  distances    Calculate team travel distances (uses routed cache if present)"
	@echo "  routed-distances  Build the global OSRM routed distance/duration matrix"
	@echo "                    (one-off; requires osrm-routed running on :5000)"
	@echo "  maps         Generate interactive maps"
	@echo "  pages        Generate team pages"
	@echo "  webpages     Generate index pages"
	@echo "  all          Run the full pipeline (scrape -> maps)"
	@echo "  (optional)   FORCE=1 with scrape, addresses, geocode to overwrite outputs"
	@echo "  scrape-fixtures  Scrape fixtures from RFU (local only)"
	@echo "  match-day    Generate match-day map with date dropdown"
	@echo "  custom-map-data  Export team catalogue for custom map builder"
	@echo "  test         Run unit tests"
	@echo "  review-screenshots  PNG snapshots under dist/ -> screenshots/review/"
	@echo "  lint         Run linters"
	@echo "  clean        Remove generated output files"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

boundaries:
	python -m core.boundaries

scrape:
	python -m rugby.scrape --season $(SEASON) $(FORCE_FLAG)

addresses:
	python -m rugby.addresses --season $(SEASON) $(FORCE_FLAG)

geocode:
	python -m rugby.geocode --season $(SEASON) $(FORCE_FLAG)

distances:
	python -m rugby.distances --season $(SEASON)

# Rebuild the global routed (road) distance & duration matrix from every
# season's geocoded teams. One-off / occasional; requires a self-hosted
# OSRM instance reachable at OSRM_URL (default http://localhost:5000).
# See rugby/distances_routed.py for OSRM setup notes.
OSRM_URL ?= http://localhost:5000
routed-distances:
	python -m rugby.distances_routed --osrm-url $(OSRM_URL)

maps:
	python -m rugby.maps --season $(SEASON)

pages:
	python -m rugby.team_pages

webpages:
	python -m rugby.webpages

custom-map-data:
	python -m rugby.custom_map

all: scrape addresses geocode distances maps pages webpages custom-map-data

scrape-fixtures:
	python -m rugby.fixtures --season $(SEASON)

match-day:
	python -m rugby.match_day --season $(SEASON)

review-screenshots:
	python scripts/capture_review_screenshots.py

test:
	python -m pytest tests/ -v

lint:
	ruff check .
	black --check .
	isort --check .

clean:
	rm -rf dist/
