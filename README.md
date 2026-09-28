# E-Sbirka OpenData pipeline

> Legacy: this was part of an earlier version of the Aturno (Lexio) stack and is no longer in use. It is kept for reference.

Python scripts that download the E-Sbirka OpenData dumps (the official Czech collection of laws) and turn them into one JSON file per legal act, with section-level text chunks and a manifest. Written to run in Google Colab with Google Drive as storage.

## Stack

- Python 3
- ijson for streaming large JSON-LD files
- requests, tqdm

## Getting started

In Google Colab (a CPU runtime is enough; the pipeline needs about 3 to 4 GB of RAM):

```python
from google.colab import drive
drive.mount('/content/drive')

!git clone https://github.com/aveekpatra/esbirka-opendata-pipeline-legacy.git
%cd esbirka-opendata-pipeline-legacy
!pip install -q -r requirements.txt
!python run_pipeline.py
```

Output goes to `/content/drive/MyDrive/esbirka`. To run elsewhere, change `BASE_DIR` in `config.py`.

Options:

```bash
python run_pipeline.py                   # full run
python run_pipeline.py --limit 1000      # process only 1000 fragments (test)
python run_pipeline.py --skip-download   # use already downloaded data files
python run_pipeline.py --skip-index      # use cached indexes
python run_pipeline.py --batch-size 200  # acts buffered before each save (default 500)
```

## How it works

1. Download: fetches five `.jsonld.gz` files from `https://opendata.eselpoint.cz/datove-sady-esbirka/` (`001PravniAktZneni`, `002PravniAkt`, `003PravniAktZneniFragment`, `004PravniAktFragment`, `014CiselnikTypZneni`), with resume support, and decompresses them (about 50 GB).
2. Index: streams the fragment file (about 32 GB) to build a fragment index (document, ELI, hierarchy, section citation, position, effective date) and an act name index. Large files on Drive are first copied to the Colab local disk.
3. Process: streams the fragment text file, strips HTML, keeps current consolidated versions, and writes each act to `output/acts/<act>.json` with its versions and chunks (chunk id, section citation, hierarchy path, text).
4. Writes `output/_sync/manifest.json` listing every act and `sync_state.json` with the last run time.

Every step writes checkpoints, so an interrupted run continues where it stopped. Logs go to `logs/pipeline_full.log` and `logs/pipeline_errors.log`.

## Output layout

```
esbirka/
  data/          downloaded .jsonld files
  cache/         fragment_index.json, act_name_index.json
  output/acts/   one JSON file per act, for example sb_2006_262.json
  output/_sync/  manifest.json, sync_state.json
  logs/
  checkpoints/
```

## Project structure

```
config.py              Paths, OpenData settings, logging
downloader.py          Download and decompress with resume
index_builder.py       Fragment and act name indexes
fragment_processor.py  Per-act JSON output and manifest
run_pipeline.py        CLI entry point
```
