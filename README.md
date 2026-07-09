# Komoot2Sigma

Transfer planned routes from [Komoot](https://www.komoot.de) to [Sigma Data Cloud](https://www.sigma-data-cloud.com) (for use with SIGMA ROX and SIGMA RIDE devices).

## Features

- **Sync** planned Komoot routes to Sigma Cloud (only uploads new routes)
- **Transfer** individual routes or all planned routes
- **Upload** local GPX files directly to Sigma Cloud
- **List** your planned Komoot tours
- Converts GPX to Sigma's native STF format (zipped) for best compatibility
- Falls back to raw GPX upload if STF conversion fails
- Deterministic route IDs ensure no duplicates across runs

## Setup

```bash
# Clone the repository
git clone <repo-url>
cd Komoot2Sigma

# Create a virtual environment and install
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

pip install -e .
```

## Usage

### 1. Authenticate

```bash
# Log in to Komoot
komoot2sigma login komoot --email your@email.com --password yourpassword

# Log in to Sigma Cloud
komoot2sigma login sigma --email your@email.com --password yourpassword
```

You can omit `--email` and `--password` for interactive input.

### 2. Sync (recommended)

The `sync` command fetches your planned tours from Komoot, checks which ones already exist on Sigma Cloud, and only uploads the missing ones:

```bash
# Preview what would be synced
komoot2sigma sync --dry-run

# Sync planned routes to Sigma Cloud
komoot2sigma sync
```

### 3. Other commands

```bash
# List planned tours
komoot2sigma list

# List all tours (including recorded)
komoot2sigma list --all-tours

# Transfer a specific tour by ID
komoot2sigma transfer 12345678

# Transfer all planned tours (without deduplication check)
komoot2sigma transfer --all

# Upload a local GPX file
komoot2sigma upload route.gpx --name "My Route"
```

### Verbose mode

Add `-v` before any command for detailed output:

```bash
komoot2sigma -v sync --dry-run
```

## How it works

1. Authenticates with Komoot via their API (email/password -> session token)
2. Authenticates with Sigma Cloud via OAuth2 (headless browser-less flow)
3. For sync: queries Sigma Cloud's `/sync` endpoint to get existing route GUIDs
4. Downloads planned routes as GPX from Komoot
5. Converts GPX to Sigma's STF (Sigma Track File) XML format
6. Zips the STF and uploads via multipart POST to Sigma Cloud's upload endpoint

Routes are assigned deterministic GUIDs based on their Komoot tour ID, so the same route always maps to the same Sigma GUID. This enables reliable deduplication without needing to compare route names or coordinates.

## Credits

This project builds on the work of:

- **[KomootGPX](https://github.com/timschneeb/KomootGPX/)** by Tim Schneeberger — used as a dependency for Komoot API interaction (authentication, tour listing)
- **[gpx2stf](https://github.com/the666bbq/gpx2stf/)** — reference for the Sigma Track File (STF) XML format used by SIGMA RIDE devices

## License

MIT
