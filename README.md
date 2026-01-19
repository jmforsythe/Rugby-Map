# English Rugby Union Team Mapping

Interactive maps showing the geographic distribution of English rugby union teams across tiers and counties.

## Setup

### Prerequisites

- Python 3.12+

### Installation

1. Clone or download this repository

2. Install Python dependencies:
```bash
pip install folium shapely scipy numpy requests beautifulsoup4
```

### Download Boundary Data

The project uses boundary data from the ONS Open Geography portal. Download the required files:

```bash
python download_boundaries.py
```

This downloads ITL1, ITL2, ITL3, and Countries boundaries to the `boundaries/` directory.

**Detail Level Options:**
- `--detail BGC` (default): Generalised Clipped - smaller files, faster download
- `--detail BFC`: Full Clipped - more detailed boundaries
- `--detail BSC`: Super Generalised - very simplified
- `--detail BUC`: Ultra Generalised - smallest files
- `--detail BFE`: Full Extent - most detailed, largest files

Example:
```bash
python download_boundaries.py --detail BFC
```

## Data Pipeline

Pre-computed geographic data for each league is included in `geocoded_teams/` as getting the data from the RFU can be difficult.

The project follows a multi-stage pipeline to collect and process team data. All commands support a `--season` parameter to specify which season to process (default: 2025-2026).

### 1. Scrape League Data
```bash
python scrape_leagues_teams.py --season 2025-2026
```

Scrapes the RFU website for all leagues and teams, saving to `league_data/[season]/` directory.
This step can fail due to rate-limiting / anti-bot detection.

**Options:**
- `--season YYYY-YYYY`: Season to scrape (e.g., 2024-2025, 2025-2026). Default: 2025-2026

### 2. Fetch Team Addresses
```bash
python fetch_addresses.py --season 2025-2026
```

Fetches physical addresses from RFU team profile pages. This step is free (no API calls) and caches results in `club_address_cache.json`.
This step can fail due to rate-limiting / anti-bot detection.

**Options:**
- `--season YYYY-YYYY`: Season to process. Default: 2025-2026
- `--workers N`: Max concurrent requests (default: 7)
- `--delay SECONDS`: Delay between requests (default: 2.0)
- `--retries N`: Max retries for failed requests (default: 3)
- `--league NAME`: Process only a single league

### 3. Geocode Addresses
```bash
python geocode_addresses.py --season 2025-2026
```

Converts addresses to coordinates using **OpenStreetMap Nominatim API** (free, no API key required).

**Options:**
- `--season YYYY-YYYY`: Season to process. Default: 2025-2026
- `--workers N`: Max concurrent geocoding requests (default: 10)
- `--google-retries N`: Retries for transient failures (default: 3)
- `--league NAME`: Process only a single league

### 4. Generate Maps
```bash
python make_tier_maps.py --season 2025-2026 --all-tiers
```

Creates all interactive maps with Voronoi diagrams.

**Options:**
- `--season YYYY-YYYY`: Season to process. Default: 2025-2026
- `--no-debug`: Exclude debug boundary layers (ITL1, ITL2, ITL3) for cleaner production maps
- `--tiers TIER [TIER ...]`: Generate specific tiers only (e.g., 'Premiership' 'Championship')
- `--mens`: Generate men's tier maps (individual)
- `--womens`: Generate women's tier maps (individual)
- `--all-tiers`: Generate all-tiers combined maps
- `--all-tiers-mens`: Generate men's all-tiers map only
- `--all-tiers-womens`: Generate women's all-tiers map only

**Features:**
- Team markers with RFU profile links and fallback logos
- Checkbox controls for showing/hiding leagues or tiers

## File Structure

```
mapping/
├── README.md                      # This file
├── utils.py                       # Shared type definitions and utilities
├── download_boundaries.py         # Download ONS boundary data
├── scrape_leagues_teams.py        # Scrape RFU for teams
├── fetch_addresses.py             # Fetch addresses from RFU
├── geocode_addresses.py           # Geocode with OpenStreetMap Nominatim
├── make_tier_maps.py              # Generate maps
├── boundaries/                    # ONS boundary GeoJSON files (gitignored)
│   ├── ITL_1.geojson
│   ├── ITL_2.geojson
│   ├── ITL_3.geojson
│   └── countries.geojson
├── league_data/                   # Scraped league/team data
│   └── [season]/
│       └── [league].json
├── team_addresses/                # Team addresses from RFU
│   └── [season]/
│       └── [league].json
├── geocoded_teams/                # Geocoded team coordinates
│   └── [season]/
│       └── [league].json
└── tier_maps/                     # Generated HTML maps (gitignored)
    └── [season]/
        ├── Counties_1.html
        ├── Counties_2.html
        ├── ...
        └── All_Tiers.html
```

## Multi-Season Support

All data and outputs are now organized by season. To work with a different season:

1. Scrape data for the desired season:
   ```bash
   python scrape_leagues_teams.py --season 2024-2025
   ```

2. Process the data through the pipeline with the same `--season` parameter

3. Data from different seasons is kept separate in season-specific subdirectories, allowing you to:
   - Compare team distributions across seasons
   - Track changes in league structures
   - Generate historical maps

## License & Data Sources

- **Boundary Data**: © Crown copyright and database rights, Office for National Statistics
- **Team Data**: Scraped from England Rugby (RFU) website
- **Geocoding**: OpenStreetMap contributors
- **Map Tiles**: © OpenStreetMap contributors, © CartoDB
