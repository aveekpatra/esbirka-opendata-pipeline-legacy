# E-Sbírka OpenData Pipeline

Scripts for processing Czech legal documents from e-Sbírka OpenData into sync-compatible JSON files.

## Quick Start (Google Colab)

> ⚠️ **Use CPU runtime** — this pipeline is CPU/IO-bound, GPU won't help.  
> Requires ~3-4GB RAM (loads fragment index into memory).

```python
# 1. Mount Google Drive
from google.colab import drive
drive.mount('/content/drive')

# 2. Clone repository
!git clone https://github.com/aveekpatra/github-esbirka.git
%cd github-esbirka

# 3. Install dependencies
!pip install -q ijson tqdm requests

# 4. Run the pipeline
!python run_pipeline.py
```

## Scripts

| Script | Purpose |
|--------|---------|
| `config.py` | Paths, settings, dual logging (full + errors) |
| `downloader.py` | Downloads OpenData files with resume support |
| `index_builder.py` | Builds fragment & act name indexes |
| `fragment_processor.py` | Creates per-act JSON files |
| `run_pipeline.py` | Main entry point (CLI) |

## Command Line Options

```bash
python run_pipeline.py                    # Full run
python run_pipeline.py --limit 1000       # Test with 1000 fragments
python run_pipeline.py --skip-download    # Use existing data files
python run_pipeline.py --batch-size 200   # Smaller batches (less memory)
```

## Output Structure

```
/content/drive/MyDrive/esbirka/
├── data/                    # Downloaded .jsonld files (~50GB)
│   ├── 001PravniAktZneni.jsonld
│   ├── 002PravniAkt.jsonld
│   ├── 003PravniAktZneniFragment.jsonld
│   ├── 004PravniAktFragment.jsonld
│   └── 014CiselnikTypZneni.jsonld
├── cache/                   # Pre-built indexes (~1.5GB)
│   ├── fragment_index.json
│   └── act_name_index.json
├── output/
│   ├── acts/               # Per-act JSON files
│   │   ├── sb_2006_262.json
│   │   └── ...
│   └── _sync/
│       ├── manifest.json   # Index of all acts
│       └── sync_state.json # Last sync timestamp
├── logs/
│   ├── pipeline_full.log   # All messages
│   └── pipeline_errors.log # Warnings/errors only
└── checkpoints/            # Resume support
    └── *.json
```

## Features

- ✅ **Resume support**: Interrupted? Just run again — continues from checkpoint
- ✅ **Memory efficient**: Streams large files, never loads entirely in memory
- ✅ **Dual logging**: Full log for debugging, error log for quick checks
- ✅ **Progress bars**: Shows ETA for long operations
- ✅ **Colab optimized**: Works within free tier limits

## Estimated Times (Colab Free Tier)

| Step | Time |
|------|------|
| Download (~50GB compressed) | 1-2 hours |
| Build fragment index (32GB file) | 30-60 minutes |
| Process fragments (5.9GB file) | 2-4 hours |
| **Total** | **4-7 hours** |

## Requirements

```
ijson>=3.0
tqdm>=4.0
requests>=2.20
```

## License

MIT
