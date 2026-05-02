# Projected 2026-2027 English Rugby Men's League Assignments

Generated automatically from 2025-2026 league table order in `data/rugby/geocoded_teams/` (same order as teams in each league JSON).
Rules applied from `tier_assignment_rules.md`.

## Assumptions

- **Premiership data unavailable** — Championship 1st place stays at Tier 2. No team promoted from Championship to Premiership; no team relegated from Premiership to Championship.
- **Play-off default heuristic** — every play-off participant remains at their current tier (the statistically most likely individual outcome).
- **Regional 2 Survival Play-Off** — 10th beats 11th (higher position wins); 11th is relegated.
- **Counties 2 → Counties 1 (data only)** — 23 feeder mapping(s) in `c2_to_c1_<next-season>.json`; optional `c1_c2_promotion_quotas_<next>.json`. **This document does not list Counties 2 (tier 8) league tables.** Promotions from tier 8 appear under the destination Counties 1 league when the `c2_to_c1` map resolves; otherwise they stay in the pooled "Promoted to Tier 7" section with source labelled Tier 8 only.
- **BPR resolved** — the 5 Counties 1 runners-up promoted via Best Playing Record are Dinnington, Finchley, Ryton, Warrington, and Weybridge Vandals.
- **Counties 1 scheduled downs** — after standard promotion rules and BPR, extra relegations to Counties 2 are applied by division using fixed slot counts (bottom of table within each league); shown as *Auto-relegation (scheduled Counties 1 downs)*.

---

## Tier 2 — Championship (14 teams)

### Staying in Championship (13 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Ealing Trailfinders | 1st |
| 2 | Bedford Blues | 2nd |
| 3 | Coventry | 3rd |
| 4 | Worcester Warriors | 4th |
| 5 | Chinnor | 5th |
| 6 | Hartpury RFC | 6th |
| 7 | Cornish Pirates | 7th |
| 8 | Nottingham | 8th |
| 9 | Ampthill | 9th |
| 10 | Doncaster Knights | 10th |
| 11 | Caldy | 11th |
| 12 | Richmond | 12th |
| 13 | London Scottish | 13th |

### Promoted to Tier 2 (1 teams) — holding league "Championship Promoted"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Rotherham Titans | National League 1 (1st) | Auto-promotion (subject to MOS) |

### Relegated from Tier 2 (1 teams) → Tier 3

| Team | From League | Mechanism |
|------|-------------|-----------|
| Cambridge | Championship (14th) | Auto-relegation |

**Championship total: 13 staying + 1 promoted in = 14**

---

## Tier 3 — National League 1 (14 teams)

### Staying in National League 1 (10 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Blackheath | 2nd |
| 2 | Plymouth Albion | 3rd |
| 3 | Rosslyn Park | 4th |
| 4 | Sale FC | 5th |
| 5 | Bishop's Stortford | 6th |
| 6 | Rams | 7th |
| 7 | Tonbridge Juddians | 8th |
| 8 | Leeds Tykes | 9th |
| 9 | Dings Crusaders | 10th |
| 10 | Birmingham Moseley | 11th |

### Promoted to Tier 3 (3 teams) — holding league "National League 1 Promoted"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Bury St Edmunds | National League 2 East (1st) | Auto-promotion |
| Camborne | National League 2 West (1st) | Auto-promotion |
| Sheffield | National League 2 North (1st) | Auto-promotion |

### Relegated to Tier 3 (1 teams) — holding league "National League 1 Relegated"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Cambridge | Championship (14th) | Auto-relegation |

### Relegated from Tier 3 (3 teams) → Tier 4

| Team | From League | Mechanism |
|------|-------------|-----------|
| Clifton | National League 1 (12th) | Auto-relegation |
| Sedgley Park | National League 1 (13th) | Auto-relegation |
| Leicester Lions | National League 1 (14th) | Auto-relegation |

**National League 1 total: 10 staying + 3 promoted in + 1 relegated in = 14**

---

## Tier 4 — National League 2 (42 teams)

### National League 2 East — Staying (11 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Oundle | 2nd |
| 2 | Old Albanians | 3rd |
| 3 | Barnes | 4th |
| 4 | Canterbury | 5th |
| 5 | Dorking | 6th |
| 6 | Westcombe Park | 7th |
| 7 | Havant | 8th |
| 8 | London Welsh | 9th |
| 9 | Guernsey | 10th |
| 10 | Esher | 11th |
| 11 | Henley | 12th |

### National League 2 North — Staying (11 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Tynedale | 2nd |
| 2 | Macclesfield | 3rd |
| 3 | Hull Ionians | 4th |
| 4 | Darlington Mowden Park | 5th |
| 5 | Fylde | 6th |
| 6 | Wharfedale | 7th |
| 7 | Sheffield Tigers | 8th |
| 8 | Preston Grasshoppers | 9th |
| 9 | Billingham | 10th |
| 10 | Otley | 11th |
| 11 | Rossendale | 12th |

### National League 2 West — Staying (11 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Luctonians | 2nd |
| 2 | Hinckley | 3rd |
| 3 | Taunton Titans | 4th |
| 4 | Cinderford | 5th |
| 5 | Hornets | 6th |
| 6 | Barnstaple | 7th |
| 7 | Old Redcliffians | 8th |
| 8 | Lymm | 9th |
| 9 | Redruth | 10th |
| 10 | Chester | 11th |
| 11 | Exeter University | 12th |

### Promoted to Tier 4 (6 teams) — holding league "National League 2 Promoted"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Bournville | Regional 1 Midlands (1st) | Auto-promotion |
| Colchester | Regional 1 South East (1st) | Auto-promotion |
| Devonport Services | Regional 1 Tribute Ale South West (1st) | Auto-promotion |
| Heath | Regional 1 North East (1st) | Auto-promotion |
| Huddersfield | Regional 1 North West (1st) | Auto-promotion |
| Jersey Rugby Football Club | Regional 1 South Central (1st) | Auto-promotion |

### Relegated to Tier 4 (3 teams) — holding league "National League 2 Relegated"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Clifton | National League 1 (12th) | Auto-relegation |
| Leicester Lions | National League 1 (14th) | Auto-relegation |
| Sedgley Park | National League 1 (13th) | Auto-relegation |

### Relegated from Tier 4 (6 teams) → Tier 5

| Team | From League | Mechanism |
|------|-------------|-----------|
| Sevenoaks | National League 2 East (13th) | Auto-relegation |
| Oxford Harlequins | National League 2 East (14th) | Auto-relegation |
| Scunthorpe | National League 2 North (13th) | Auto-relegation |
| Hull | National League 2 North (14th) | Auto-relegation |
| Loughborough Students | National League 2 West (13th) | Auto-relegation |
| Syston | National League 2 West (14th) | Auto-relegation |

**National League 2 total: 33 staying + 6 promoted in + 3 relegated in = 42**

---

## Tier 5 — Regional 1 (72 teams)

### Regional 1 Midlands — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Newport (Salop) | 2nd |
| 2 | Stourbridge | 3rd |
| 3 | Newent | 4th |
| 4 | Bromsgrove | 5th |
| 5 | Lichfield | 6th |
| 6 | Bridgnorth | 7th |
| 7 | Banbury | 8th |
| 8 | Lutterworth | 9th |
| 9 | Drybrook | 10th |

### Regional 1 North East — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Alnwick | 2nd |
| 2 | Harrogate | 3rd |
| 3 | Ilkley | 4th |
| 4 | Blaydon | 5th |
| 5 | Driffield | 6th |
| 6 | Sandal | 7th |
| 7 | Kendal | 8th |
| 8 | Middlesbrough | 9th |
| 9 | York | 10th |

### Regional 1 North West — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Anselmians | 2nd |
| 2 | Stockport | 3rd |
| 3 | Burton | 4th |
| 4 | Leek | 5th |
| 5 | Blackburn | 6th |
| 6 | Bowdon | 7th |
| 7 | Rochdale | 8th |
| 8 | Manchester | 9th |
| 9 | Wirral | 10th |

### Regional 1 South Central — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | London Scottish Lions | 2nd |
| 2 | Tunbridge Wells | 3rd |
| 3 | Old Alleynians | 4th |
| 4 | CS Stags 1863 | 5th |
| 5 | Bracknell | 6th |
| 6 | Farnham | 7th |
| 7 | Worthing | 8th |
| 8 | Wimbledon | 9th |
| 9 | Maidenhead | 10th |

### Regional 1 South East — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Letchworth Garden City | 2nd |
| 2 | HUEL Tring | 3rd |
| 3 | Southend Saxons | 4th |
| 4 | Sudbury | 5th |
| 5 | North Walsham | 6th |
| 6 | Hertford | 7th |
| 7 | Old Northamptonians | 8th |
| 8 | Westcliff | 9th |
| 9 | Amersham & Chiltern | 10th |

### Regional 1 Tribute Ale South West — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Topsham | 2nd |
| 2 | Brixham | 3rd |
| 3 | Exmouth | 4th |
| 4 | St Austell | 5th |
| 5 | Royal Wootton Bassett | 6th |
| 6 | Lydney | 7th |
| 7 | Launceston | 8th |
| 8 | Sidmouth | 9th |
| 9 | Chew Valley | 10th |

### Promoted to Tier 5 (12 teams) — holding league "Regional 1 Promoted"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Bournemouth | Regional 2 South Central (1st) | Auto-promotion |
| Brunel University | Regional 2 Thames (1st) | Auto-promotion |
| Dronfield | Regional 2 Midlands North (1st) | Auto-promotion |
| Eton Manor | Regional 2 Anglia (1st) | Auto-promotion |
| Moseley Oak | Regional 2 Midlands West (1st) | Auto-promotion |
| North Ribblesdale | Regional 2 North West (1st) | Auto-promotion |
| Northampton Old Scouts | Regional 2 Midlands East (1st) | Auto-promotion |
| Northern | Regional 2 North (1st) | Auto-promotion |
| Scarborough | Regional 2 North East (1st) | Auto-promotion |
| Sidcup | Regional 2 South East (1st) | Auto-promotion |
| Trowbridge | Regional 2 Tribute Ale Severn (1st) | Auto-promotion |
| Weston-super-Mare | Regional 2 Tribute Ale South West (1st) | Auto-promotion |

### Relegated to Tier 5 (6 teams) — holding league "Regional 1 Relegated"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Hull | National League 2 North (14th) | Auto-relegation |
| Loughborough Students | National League 2 West (13th) | Auto-relegation |
| Oxford Harlequins | National League 2 East (14th) | Auto-relegation |
| Scunthorpe | National League 2 North (13th) | Auto-relegation |
| Sevenoaks | National League 2 East (13th) | Auto-relegation |
| Syston | National League 2 West (14th) | Auto-relegation |

### Relegated from Tier 5 (12 teams) → Tier 6

| Team | From League | Mechanism |
|------|-------------|-----------|
| Dudley Kingswinford | Regional 1 Midlands (11th) | Auto-relegation |
| Nuneaton | Regional 1 Midlands (12th) | Auto-relegation |
| Penrith | Regional 1 North East (11th) | Auto-relegation |
| Cleckheaton | Regional 1 North East (12th) | Auto-relegation |
| Long Eaton | Regional 1 North West (11th) | Auto-relegation |
| Derby | Regional 1 North West (12th) | Auto-relegation |
| Hammersmith & Fulham | Regional 1 South Central (11th) | Auto-relegation |
| Camberley | Regional 1 South Central (12th) | Auto-relegation |
| Medway | Regional 1 South East (11th) | Auto-relegation |
| Shelford | Regional 1 South East (12th) | Auto-relegation |
| Marlborough | Regional 1 Tribute Ale South West (11th) | Auto-relegation |
| Matson | Regional 1 Tribute Ale South West (12th) | Auto-relegation |

**Regional 1 total: 54 staying + 12 promoted in + 6 relegated in = 72**

---

## Tier 6 — Regional 2 (144 teams)

### Regional 2 Anglia — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | H.A.C. | 2nd |
| 2 | Brentwood | 3rd |
| 3 | Wymondham | 4th |
| 4 | Wanstead | 5th |
| 5 | Harlow | 6th |
| 6 | Rochford Hundred | 7th |
| 7 | Woodford | 8th |
| 8 | Chelmsford | 9th |
| 9 | Braintree | 10th |

### Regional 2 Midlands East — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Market Harborough | 2nd |
| 2 | Broadstreet | 3rd |
| 3 | Bedford Athletic | 4th |
| 4 | Peterborough | 5th |
| 5 | Stamford | 6th |
| 6 | Kettering | 7th |
| 7 | Oadby Wyggestonians | 8th |
| 8 | Olney | 9th |
| 9 | Old Coventrians | 10th |

### Regional 2 Midlands North — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | West Bridgford | 2nd |
| 2 | Walsall | 3rd |
| 3 | Melbourne | 4th |
| 4 | Stoke on Trent | 5th |
| 5 | Sutton Coldfield | 6th |
| 6 | Ilkeston | 7th |
| 7 | Matlock | 8th |
| 8 | Paviors | 9th |
| 9 | Old Saltleians | 10th |

### Regional 2 Midlands West — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Stow-on-the-Wold | 2nd |
| 2 | Luctonians II | 3rd |
| 3 | Hereford | 4th |
| 4 | Silhillians | 5th |
| 5 | Edwardians | 6th |
| 6 | Ludlow | 7th |
| 7 | Kenilworth | 8th |
| 8 | Malvern | 9th |
| 9 | Shipston on Stour | 10th |

### Regional 2 North — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Consett | 2nd |
| 2 | Percy Park | 3rd |
| 3 | Keswick | 4th |
| 4 | Sunderland | 5th |
| 5 | Upper Eden | 6th |
| 6 | West Hartlepool | 7th |
| 7 | Morpeth | 8th |
| 8 | Durham City | 9th |
| 9 | Guisborough | 10th |

### Regional 2 North East — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Pocklington | 2nd |
| 2 | Old Brodleians | 3rd |
| 3 | Morley | 4th |
| 4 | Selby | 5th |
| 5 | Moortown | 6th |
| 6 | Malton and Norton | 7th |
| 7 | Bradford Salem | 8th |
| 8 | Pontefract | 9th |
| 9 | Wetherby | 10th |

### Regional 2 North West — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Vale of Lune | 2nd |
| 2 | Sandbach | 3rd |
| 3 | Widnes | 4th |
| 4 | Douglas (I.O.M.) | 5th |
| 5 | Burnage | 6th |
| 6 | Winnington Park | 7th |
| 7 | Birkenhead Park | 8th |
| 8 | Firwood Waterloo | 9th |
| 9 | Northwich | 10th |

### Regional 2 South Central — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Winchester | 2nd |
| 2 | Wimborne | 3rd |
| 3 | Old Tiffinians | 4th |
| 4 | Petersfield | 5th |
| 5 | Tottonians | 6th |
| 6 | Havant II | 7th |
| 7 | Twickenham | 8th |
| 8 | Chobham | 9th |
| 9 | Guildford | 10th |

### Regional 2 South East — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Brighton | 2nd |
| 2 | Gravesend | 3rd |
| 3 | Horsham | 4th |
| 4 | Old Colfeians | 5th |
| 5 | Battersea Ironsides | 6th |
| 6 | Sutton & Epsom | 7th |
| 7 | Beckenham | 8th |
| 8 | Bromley | 9th |
| 9 | Dartfordians | 11th |

### Regional 2 Thames — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Beaconsfield | 2nd |
| 2 | Old Priorians | 3rd |
| 3 | Belsize Park | 4th |
| 4 | Aylesbury Men I | 5th |
| 5 | Marlow | 6th |
| 6 | Teddington | 7th |
| 7 | Harpenden | 8th |
| 8 | Grasshoppers | 9th |
| 9 | London Irish Wild Geese | 10th |

### Regional 2 Tribute Ale Severn — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Chippenham | 2nd |
| 2 | Cleve | 3rd |
| 3 | Keynsham | 4th |
| 4 | Newbury Blues | 5th |
| 5 | Old Centralians | 6th |
| 6 | Thornbury | 7th |
| 7 | Longlevens | 8th |
| 8 | Witney | 9th |
| 9 | Royal Wootton Bassett II | 11th |

### Regional 2 Tribute Ale South West — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Tiverton | 2nd |
| 2 | Wadebridge Camels | 3rd |
| 3 | Okehampton | 4th |
| 4 | Crediton | 5th |
| 5 | Ivybridge | 6th |
| 6 | Winscombe | 7th |
| 7 | Penzance & Newlyn | 8th |
| 8 | North Petherton | 9th |
| 9 | Teignmouth | 10th |

### Best Playing Record into Tier 6 (5 teams)

| Team | Counties 1 league | Position |
|------|-------------------|---------|
| Ryton | Counties 1 Durham & Northumberland | 2nd |
| Finchley | Counties 1 Middx | 2nd |
| Weybridge Vandals | Counties 1 Surrey/Sussex | 2nd |
| Dinnington | Counties 1 Yorkshire | 2nd |
| Warrington | Counties 1 adm Lancashire & Cheshire | 2nd |

### Promoted to Tier 6 (19 teams) — holding league "Regional 2 Promoted"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Bognor | Counties 1 Hampshire (1st) | Auto-promotion |
| Bury St Edmunds II | Counties 1 Eastern Counties (1st) | Auto-promotion |
| Cheshunt | Counties 1 Herts (1st) | Auto-promotion |
| Chinnor III | Counties 1 Tribute Ale Southern North (1st) | Auto-promotion |
| Cobham | Counties 1 Surrey/Sussex (1st) | Auto-promotion |
| Ealing Trailfinders 1871 | Counties 1 Middx (1st) | Auto-promotion |
| Hinckley II | Counties 1 Midlands East (South) (1st) | Auto-promotion |
| Kirkby Lonsdale | Counties 1 Cumbria (1st) | Auto-promotion |
| Nailsea & Backwell | Counties 1 Tribute Ale Western North (1st) | Auto-promotion |
| Novocastrians | Counties 1 Durham & Northumberland (1st) | Auto-promotion |
| Old Elthamians | Counties 1 Kent (1st) | Auto-promotion |
| Sherborne | Counties 1 Tribute Ale Southern South (1st) | Auto-promotion |
| Thurrock | Counties 1 Essex (1st) | Auto-promotion |
| University of Nottingham | Counties 1 Midlands East (North) (1st) | Auto-promotion |
| Wath Upon Dearne | Counties 1 Yorkshire (1st) | Auto-promotion |
| Whitchurch | Counties 1 Midlands West (North) (1st) | Auto-promotion |
| Wilmslow | Counties 1 adm Lancashire & Cheshire (1st) | Auto-promotion |
| Wiveliscombe | Counties 1 Tribute Ale Western West (1st) | Auto-promotion |
| Worcester | Counties 1 Midlands West (South) (1st) | Auto-promotion |

### Relegated to Tier 6 (12 teams) — holding league "Regional 2 Relegated"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Camberley | Regional 1 South Central (12th) | Auto-relegation |
| Cleckheaton | Regional 1 North East (12th) | Auto-relegation |
| Derby | Regional 1 North West (12th) | Auto-relegation |
| Dudley Kingswinford | Regional 1 Midlands (11th) | Auto-relegation |
| Hammersmith & Fulham | Regional 1 South Central (11th) | Auto-relegation |
| Long Eaton | Regional 1 North West (11th) | Auto-relegation |
| Marlborough | Regional 1 Tribute Ale South West (11th) | Auto-relegation |
| Matson | Regional 1 Tribute Ale South West (12th) | Auto-relegation |
| Medway | Regional 1 South East (11th) | Auto-relegation |
| Nuneaton | Regional 1 Midlands (12th) | Auto-relegation |
| Penrith | Regional 1 North East (11th) | Auto-relegation |
| Shelford | Regional 1 South East (12th) | Auto-relegation |

### Relegated from Tier 6 (24 teams) → Tier 7

| Team | From League | Mechanism |
|------|-------------|-----------|
| Norwich | Regional 2 Anglia (11th) | Survival PO loser |
| Holt | Regional 2 Anglia (12th) | Auto-relegation |
| Daventry | Regional 2 Midlands East (11th) | Survival PO loser |
| Wellingborough | Regional 2 Midlands East (12th) | Auto-relegation |
| Newark | Regional 2 Midlands North (11th) | Survival PO loser |
| Belgrave | Regional 2 Midlands North (12th) | Auto-relegation |
| Stratford Upon Avon | Regional 2 Midlands West (11th) | Survival PO loser |
| Old Halesonians | Regional 2 Midlands West (12th) | Auto-relegation |
| Aspatria | Regional 2 North (11th) | Survival PO loser |
| Wigton | Regional 2 North (12th) | Auto-relegation |
| Old Crossleyans | Regional 2 North East (11th) | Survival PO loser |
| Doncaster Phoenix | Regional 2 North East (12th) | Auto-relegation |
| West Park (St Helens) | Regional 2 North West (11th) | Survival PO loser |
| Altrincham Kersal | Regional 2 North West (12th) | Auto-relegation |
| Reeds Weybridge | Regional 2 South Central (11th) | Survival PO loser |
| Ellingham & Ringwood | Regional 2 South Central (12th) | Auto-relegation |
| Canterbury II | Regional 2 South East (10th) | Survival PO loser |
| Old Reigatian | Regional 2 South East (12th) | Auto-relegation |
| Windsor | Regional 2 Thames (11th) | Survival PO loser |
| Oxford Harlequins II | Regional 2 Thames (12th) | Auto-relegation |
| North Dorset | Regional 2 Tribute Ale Severn (10th) | Survival PO loser |
| Cheltenham | Regional 2 Tribute Ale Severn (12th) | Auto-relegation |
| Cullompton | Regional 2 Tribute Ale South West (11th) | Survival PO loser |
| Wellington | Regional 2 Tribute Ale South West (12th) | Auto-relegation |

**Regional 2 total: 108 staying + 24 promoted in + 12 relegated in = 144**

---

## Tier 7 — Counties 1 (214 teams)

### Counties 1 Cumbria — Staying (13 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Hawcoat Park | 2nd |
| 2 | Carlisle | 3rd |
| 3 | Cockermouth | 4th |
| 4 | Whitehaven | 5th |
| 5 | St Benedicts | 6th |
| 6 | Egremont | 7th |
| 7 | Workington | 8th |
| 8 | Ambleside | 9th |
| 9 | Windermere | 10th |
| 10 | Creighton | 11th |
| 11 | Keswick II | 12th |
| 12 | Penrith II | 13th |
| 13 | Millom | 14th |

### Counties 1 Cumbria — From Regional 2 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Aspatria | Regional 2 North (11th) | Survival PO loser |
| Wigton | Regional 2 North (12th) | Auto-relegation |

### Counties 1 Durham & Northumberland — Staying (10 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Stockton | 3rd |
| 2 | Medicals | 4th |
| 3 | Peterlee and Horden | 5th |
| 4 | Durham University | 6th |
| 5 | Gateshead | 7th |
| 6 | Darlington | 8th |
| 7 | Ponteland | 9th |
| 8 | Bishop Auckland | 10th |
| 9 | Hartlepool | 11th |
| 10 | Acklam | 12th |

### Counties 1 Durham & Northumberland — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Barnard Castle | Tier 8 (1st) | Auto-promotion |

### Counties 1 Eastern Counties — Staying (8 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Stowmarket | 2nd |
| 2 | Saffron Walden | 3rd |
| 3 | Southwold | 4th |
| 4 | West Norfolk | 5th |
| 5 | Shelford II | 6th |
| 6 | Ipswich | 7th |
| 7 | Newmarket | 8th |
| 8 | Colchester II | 9th |

### Counties 1 Eastern Counties — From Regional 2 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Holt | Regional 2 Anglia (12th) | Auto-relegation |
| Norwich | Regional 2 Anglia (11th) | Survival PO loser |

### Counties 1 Eastern Counties — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Cantabrigian | Tier 8 (1st) | Auto-promotion |

### Counties 1 Essex — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Epping Upper Clapton | 2nd |
| 2 | Romford & Gidea Park | 3rd |
| 3 | Maldon | 4th |
| 4 | Campion | 5th |
| 5 | East London | 6th |
| 6 | Billericay | 7th |
| 7 | Chingford | 8th |
| 8 | Upminster | 9th |
| 9 | Stanford Le Hope | 10th |

### Counties 1 Essex — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Burnham-on-Crouch | Tier 8 (1st) | Auto-promotion |

### Counties 1 Hampshire — Staying (8 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Portsmouth | 2nd |
| 2 | Basingstoke | 3rd |
| 3 | Andover | 4th |
| 4 | Sandown & Shanklin | 5th |
| 5 | Chichester | 6th |
| 6 | Bournemouth II | 7th |
| 7 | Winchester II | 8th |
| 8 | Millbrook | 9th |

### Counties 1 Hampshire — From Regional 2 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Ellingham & Ringwood | Regional 2 South Central (12th) | Auto-relegation |

### Counties 1 Hampshire — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Farnham II | Tier 8 (1st) | Auto-promotion |

### Counties 1 Herts — Staying (8 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Old Albanian Romans | 2nd |
| 2 | Hitchin | 3rd |
| 3 | Hertford II | 4th |
| 4 | Eton Manor II | 5th |
| 5 | Hemel Hempstead | 6th |
| 6 | Bishop's Stortford II | 7th |
| 7 | Fullerians | 8th |
| 8 | Southend II | 9th |

### Counties 1 Herts — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Datchworth | Tier 8 (1st) | Auto-promotion |

### Counties 1 Kent — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Deal & Betteshanger | 2nd |
| 2 | Heathfield & Waldron | 3rd |
| 3 | Sevenoaks II | 4th |
| 4 | Ashford | 5th |
| 5 | Dover | 6th |
| 6 | Crowborough | 7th |
| 7 | Beccehamian | 8th |
| 8 | Gillingham Anchorians | 9th |
| 9 | Park House | 10th |

### Counties 1 Kent — From Regional 2 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Canterbury II | Regional 2 South East (10th) | Survival PO loser |

### Counties 1 Kent — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Tonbridge Juddians II | Tier 8 (1st) | Auto-promotion |

### Counties 1 Middx — Staying (8 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Old Haberdashers | 3rd |
| 2 | Hampstead | 4th |
| 3 | Chiswick | 5th |
| 4 | Old Streetonians | 6th |
| 5 | Ruislip | 7th |
| 6 | Wasps FC | 8th |
| 7 | U.C.S. | 9th |
| 8 | Saracens Amateurs | 10th |

### Counties 1 Middx — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Hackney | Tier 8 (1st) | Auto-promotion |

### Counties 1 Midlands East (North) — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Market Rasen & Louth | 2nd |
| 2 | Bourne | 3rd |
| 3 | Loughborough University II | 4th |
| 4 | Spalding | 5th |
| 5 | Southwell | 6th |
| 6 | Lincoln | 7th |
| 7 | Mellish | 8th |
| 8 | Keyworth | 9th |
| 9 | Kesteven | 10th |

### Counties 1 Midlands East (North) — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Mansfield | Tier 8 (1st) | Auto-promotion |

### Counties 1 Midlands East (South) — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Newbold on Avon | 2nd |
| 2 | Towcestrians | 3rd |
| 3 | Nuneaton Old Edwardians | 4th |
| 4 | Leicester Forest | 5th |
| 5 | Bugbrooke | 6th |
| 6 | Vipers | 7th |
| 7 | Long Buckby | 8th |
| 8 | Market Bosworth | 9th |
| 9 | Stewarts & Lloyds | 10th |

### Counties 1 Midlands East (South) — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Oundle II | Tier 8 (1st) | Auto-promotion |

### Counties 1 Midlands West (North) — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Crewe & Nantwich | 2nd |
| 2 | Shrewsbury | 3rd |
| 3 | Spartans (Midlands) | 4th |
| 4 | Veseyans | 5th |
| 5 | Newport (Salop) II | 6th |
| 6 | Burntwood | 7th |
| 7 | Newcastle (Staffs) | 8th |
| 8 | Aston Old Edwardians | 9th |
| 9 | Stourbridge II | 10th |

### Counties 1 Midlands West (North) — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Barkers Butts | Tier 8 (1st) | Auto-promotion |

### Counties 1 Midlands West (South) — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Alcester | 2nd |
| 2 | Camp Hill | 3rd |
| 3 | Ledbury | 4th |
| 4 | Evesham | 5th |
| 5 | Bromyard | 6th |
| 6 | Earlsdon | 7th |
| 7 | Leamington | 8th |
| 8 | Manor Park | 9th |
| 9 | Old Leamingtonians | 10th |

### Counties 1 Midlands West (South) — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Trentham | Tier 8 (1st) | Auto-promotion |

### Counties 1 Surrey/Sussex — Staying (8 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Trinity | 3rd |
| 2 | Hove | 4th |
| 3 | Eastbourne | 5th |
| 4 | London Cornish | 6th |
| 5 | Old Rutlishians | 7th |
| 6 | Haywards Heath | 8th |
| 7 | Old Caterhamians | 9th |
| 8 | Old Wimbledonians | 10th |

### Counties 1 Surrey/Sussex — From Regional 2 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Old Reigatian | Regional 2 South East (12th) | Auto-relegation |
| Reeds Weybridge | Regional 2 South Central (11th) | Survival PO loser |

### Counties 1 Surrey/Sussex — From Tier 8 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Old Hamptonians | Tier 8 (1st) | Auto-promotion |
| Pulborough | Tier 8 (1st) | Auto-promotion |

### Counties 1 Tribute Ale Southern North — Staying (8 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Buckingham | 2nd |
| 2 | Henley II | 3rd |
| 3 | High Wycombe | 4th |
| 4 | Reading Abbey | 5th |
| 5 | Reading | 6th |
| 6 | Bracknell II | 7th |
| 7 | Bicester | 8th |
| 8 | Oxford | 9th |

### Counties 1 Tribute Ale Southern North — From Regional 2 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Oxford Harlequins II | Regional 2 Thames (12th) | Auto-relegation |
| Windsor | Regional 2 Thames (11th) | Survival PO loser |

### Counties 1 Tribute Ale Southern North — From Tier 8 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Banbury II | Tier 8 (1st) | Auto-promotion |
| Beaconsfield II | Tier 8 (1st) | Auto-promotion |

### Counties 1 Tribute Ale Southern South — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Walcot | 2nd |
| 2 | Devizes | 3rd |
| 3 | Frome | 4th |
| 4 | Salisbury | 5th |
| 5 | Swanage & Wareham | 6th |
| 6 | Melksham | 7th |
| 7 | Corsham | 8th |
| 8 | Trowbridge II | 9th |
| 9 | Chippenham II | 10th |

### Counties 1 Tribute Ale Southern South — From Regional 2 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| North Dorset | Regional 2 Tribute Ale Severn (10th) | Survival PO loser |

### Counties 1 Tribute Ale Southern South — From Tier 8 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Combe Down | Tier 8 (1st) | Auto-promotion |
| Dorchester | Tier 8 (1st) | Auto-promotion |

### Counties 1 Tribute Ale Western North — Staying (8 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Dings Crusaders II | 2nd |
| 2 | Midsomer Norton | 3rd |
| 3 | Clifton II | 4th |
| 4 | Old Bristolians | 5th |
| 5 | Old Redcliffians II | 6th |
| 6 | Gordano | 7th |
| 7 | Chard | 8th |
| 8 | Gordon League | 9th |

### Counties 1 Tribute Ale Western North — From Regional 2 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Cheltenham | Regional 2 Tribute Ale Severn (12th) | Auto-relegation |
| Wellington | Regional 2 Tribute Ale South West (12th) | Auto-relegation |

### Counties 1 Tribute Ale Western North — From Tier 8 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Burnham-on-Sea | Tier 8 (1st) | Auto-promotion |
| Cinderford II | Tier 8 (1st) | Auto-promotion |

### Counties 1 Tribute Ale Western West — Staying (9 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Paignton | 2nd |
| 2 | Barnstaple II | 3rd |
| 3 | Truro | 4th |
| 4 | Redruth II | 5th |
| 5 | Torquay Athletic | 6th |
| 6 | St Ives (SW) | 7th |
| 7 | Kingsbridge | 8th |
| 8 | Newton Abbot | 9th |
| 9 | Saltash | 10th |

### Counties 1 Tribute Ale Western West — From Regional 2 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Cullompton | Regional 2 Tribute Ale South West (11th) | Survival PO loser |

### Counties 1 Tribute Ale Western West — From Tier 8 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Camborne II | Tier 8 (1st) | Auto-promotion |
| Devonport Services II | Tier 8 (1st) | Auto-promotion |

### Counties 1 Yorkshire — Staying (8 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Goole | 3rd |
| 2 | Yarnbury | 4th |
| 3 | Keighley | 5th |
| 4 | West Leeds | 6th |
| 5 | Wensleydale | 7th |
| 6 | Old Rishworthian | 8th |
| 7 | Harrogate Pythons | 9th |
| 8 | Beverley | 10th |

### Counties 1 Yorkshire — From Regional 2 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Doncaster Phoenix | Regional 2 North East (12th) | Auto-relegation |
| Old Crossleyans | Regional 2 North East (11th) | Survival PO loser |

### Counties 1 Yorkshire — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Old Otliensians | Tier 8 (1st) | Auto-promotion |

### Counties 1 adm Lancashire & Cheshire — Staying (8 teams)

| # | Team | 2025-2026 Position |
|---|------|-----------------|
| 1 | Aldwinians | 3rd |
| 2 | Eccles | 4th |
| 3 | Didsbury Toc H | 5th |
| 4 | New Brighton | 6th |
| 5 | Broughton Park | 7th |
| 6 | Tarleton | 8th |
| 7 | Wigan | 9th |
| 8 | Liverpool St Helens | 10th |

### Counties 1 adm Lancashire & Cheshire — From Regional 2 (2 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Altrincham Kersal | Regional 2 North West (12th) | Auto-relegation |
| West Park (St Helens) | Regional 2 North West (11th) | Survival PO loser |

### Counties 1 adm Lancashire & Cheshire — From Tier 8 (1 teams)

| Team | From League | Mechanism |
|------|-------------|-----------|
| Southport | Tier 8 (1st) | Auto-promotion |

### Relegated to Tier 7 (6 teams) — holding league "Counties 1 Relegated"

| Team | From League | Mechanism |
|------|-------------|-----------|
| Belgrave | Regional 2 Midlands North (12th) | Auto-relegation |
| Daventry | Regional 2 Midlands East (11th) | Survival PO loser |
| Newark | Regional 2 Midlands North (11th) | Survival PO loser |
| Old Halesonians | Regional 2 Midlands West (12th) | Auto-relegation |
| Stratford Upon Avon | Regional 2 Midlands West (11th) | Survival PO loser |
| Wellingborough | Regional 2 Midlands East (12th) | Auto-relegation |

### Relegated from Tier 7 (32 teams) → Tier 8

| Team | From League | Mechanism |
|------|-------------|-----------|
| Ely | Counties 1 Eastern Counties (10th) | Auto-relegation (scheduled Counties 1 downs) |
| Ipswich Y.M. | Counties 1 Eastern Counties (11th) | Auto-relegation (scheduled Counties 1 downs) |
| North Walsham II | Counties 1 Eastern Counties (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Gosport & Fareham | Counties 1 Hampshire (10th) | Auto-relegation (scheduled Counties 1 downs) |
| Guernsey II | Counties 1 Hampshire (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Eastleigh | Counties 1 Hampshire (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Welwyn | Counties 1 Herts (10th) | Auto-relegation (scheduled Counties 1 downs) |
| Cranbrook | Counties 1 Kent (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Charlton Park | Counties 1 Kent (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Nottingham Moderns | Counties 1 Midlands East (North) (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Ashby | Counties 1 Midlands East (North) (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Biggleswade | Counties 1 Midlands East (South) (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Dunstablians | Counties 1 Midlands East (South) (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Telford Hornets | Counties 1 Midlands West (North) (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Tamworth | Counties 1 Midlands West (North) (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Kidderminster | Counties 1 Midlands West (South) (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Droitwich | Counties 1 Midlands West (South) (12th) | Auto-relegation (scheduled Counties 1 downs) |
| KCS Old Boys | Counties 1 Surrey/Sussex (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Wallingford | Counties 1 Tribute Ale Southern North (10th) | Auto-relegation (scheduled Counties 1 downs) |
| Bletchley | Counties 1 Tribute Ale Southern North (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Gosford All Blacks | Counties 1 Tribute Ale Southern North (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Swindon | Counties 1 Tribute Ale Southern South (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Marlborough II | Counties 1 Tribute Ale Southern South (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Bridgwater & Albion | Counties 1 Tribute Ale Western North (10th) | Auto-relegation (scheduled Counties 1 downs) |
| Taunton II | Counties 1 Tribute Ale Western North (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Chosen Hill Former Pupils | Counties 1 Tribute Ale Western North (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Penryn | Counties 1 Tribute Ale Western West (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Plymstock Oaks | Counties 1 Tribute Ale Western West (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Hullensians | Counties 1 Yorkshire (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Leodiensian | Counties 1 Yorkshire (12th) | Auto-relegation (scheduled Counties 1 downs) |
| Aspull | Counties 1 adm Lancashire & Cheshire (11th) | Auto-relegation (scheduled Counties 1 downs) |
| Trafford MV | Counties 1 adm Lancashire & Cheshire (12th) | Auto-relegation (scheduled Counties 1 downs) |

**Counties 1 total: 167 staying + 23 promoted in + 24 relegated in = 214**

---

## Validation Summary

| Tier | Level | Target | Confirmed | Notes |
|------|-------|--------|-----------|-------|
| 2 | Championship | 14 | **14** | 13 staying + 1 promoted + 0 relegated in. ✓ |
| 3 | National 1 | 14 | **14** | 10 staying + 3 promoted + 1 relegated in. ✓ |
| 4 | National 2 (×3) | 42 | **42** | 33 staying + 6 promoted + 3 relegated in. ✓ |
| 5 | Regional 1 (×6) | 72 | **72** | 54 staying + 12 promoted + 6 relegated in. ✓ |
| 6 | Regional 2 (×12) | 144 | **144** | 108 staying + 24 promoted + 12 relegated in. ✓ |
| 7 | Counties 1 (×19) | varies | **214** | 167 staying + 23 promoted + 24 relegated in |

### Movement Totals

| Direction | Count | Teams |
|-----------|-------|-------|
| Tier 2 → Tier 3 | 1 | Cambridge |
| Tier 3 → Tier 2 | 1 | Rotherham Titans |
| Tier 3 → Tier 4 | 3 | Clifton, Leicester Lions, Sedgley Park |
| Tier 4 → Tier 3 | 3 | Bury St Edmunds, Camborne, Sheffield |
| Tier 4 → Tier 5 | 6 | Hull, Loughborough Students, Oxford Harlequins, Scunthorpe, Sevenoaks, Syston |
| Tier 5 → Tier 4 | 6 | Bournville, Colchester, Devonport Services, Heath, Huddersfield, Jersey Rugby Football Club |
| Tier 5 → Tier 6 | 12 | 12 teams |
| Tier 6 → Tier 5 | 12 | 12 teams |
| Tier 6 → Tier 7 | 24 | 24 teams |
| Tier 7 → Tier 6 | 24 | 24 teams |
