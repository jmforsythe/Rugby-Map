# Season navigation prototypes

Minimal static sites comparing four season-switcher patterns for map headers.

| Worktree | Variant | Port |
|----------|---------|------|
| `mapping-proto-season-select` | `<select>` dropdown | **8765** |
| `mapping-proto-season-split` | Split-button crumb | **8766** |
| `mapping-proto-season-arrows` | Prev / next arrows | **8767** |
| `mapping-proto-season-menu` | `<details>` link menu | **8768** |

## Build & serve

```bash
cd prototypes/season-nav
python build.py          # uses variant.txt
python -m http.server 8765 --directory dist
```

Demo pages include Premiership maps, Fixtures maps, and season hubs. **1999-2000** has no Premiership page (tests disabled/fallback behaviour).
