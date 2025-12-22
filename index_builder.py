"""
Fragment and Act Name Index Builder
Creates mappings needed for fragment processing.
Optimized for large files (10-30GB) with checkpointing.
"""

import ijson
import json
import re
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from tqdm.auto import tqdm
from collections import defaultdict

logger = logging.getLogger(__name__)


def _copy_to_local_if_on_drive(filepath: Path) -> Path:
    """
    Copy file to local disk for faster processing if it's on Google Drive.
    Returns the local path (or original if not on Drive).
    """
    filepath = Path(filepath)
    
    # Check if file is on Google Drive mount
    if '/content/drive' not in str(filepath):
        return filepath
    
    # Local destination
    local_path = Path('/content') / filepath.name
    
    # Skip if already copied
    if local_path.exists():
        local_size = local_path.stat().st_size
        drive_size = filepath.stat().st_size
        if local_size == drive_size:
            logger.info(f"Using existing local copy: {local_path}")
            return local_path
        else:
            logger.warning(f"Local copy size mismatch, re-copying...")
            local_path.unlink()
    
    # Copy to local disk
    file_size_gb = filepath.stat().st_size / 1024 / 1024 / 1024
    logger.info(f"Copying {file_size_gb:.1f}GB to local disk for faster processing...")
    logger.info(f"  From: {filepath}")
    logger.info(f"  To: {local_path}")
    
    try:
        shutil.copy(filepath, local_path)
        logger.info(f"Copy complete! Processing will be 3-5x faster.")
        return local_path
    except Exception as e:
        logger.warning(f"Could not copy to local disk: {e}")
        logger.warning(f"Falling back to Drive (slower)")
        return filepath


def build_fragment_index(
    data_dir: Path,
    cache_dir: Path,
    checkpoint_dir: Optional[Path] = None,
    batch_save_interval: int = 100000
) -> Dict[int, Dict[str, Any]]:
    """
    Build index from 003PravniAktZneniFragment.jsonld
    Maps: fragment-id -> {document_id, eli, hierarchy, section_citation, position, effectiveness_date}
    
    This is a large operation (~30GB file, ~200M records, takes 30-60 min).
    Supports checkpointing for resume after interruption.
    
    Args:
        data_dir: Directory with OpenData files
        cache_dir: Directory to save final index
        checkpoint_dir: Directory for checkpoints (uses cache_dir if None)
        batch_save_interval: Save checkpoint every N records
        
    Returns:
        Fragment index dictionary {fragment_id: metadata_dict}
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    index_file = cache_dir / "fragment_index.json"
    checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else cache_dir
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_file = checkpoint_dir / "fragment_index_checkpoint.json"
    
    # Check if already built
    if index_file.exists():
        size_mb = index_file.stat().st_size / 1024 / 1024
        logger.info(f"Fragment index already exists: {index_file} ({size_mb:.1f} MB)")
        logger.info("Loading existing index...")
        try:
            with open(index_file, 'r', encoding='utf-8') as f:
                str_index = json.load(f)
            result = {int(k): v for k, v in str_index.items()}
            logger.info(f"Loaded {len(result):,} entries")
            return result
        except Exception as e:
            logger.error(f"Failed to load existing index: {e}")
            logger.info("Will rebuild from scratch...")
    
    zneni_frag_file = Path(data_dir) / "003PravniAktZneniFragment.jsonld"
    if not zneni_frag_file.exists():
        logger.error(f"File not found: {zneni_frag_file}")
        return {}
    
    # Copy to local disk for faster processing (if on Drive)
    zneni_frag_file = _copy_to_local_if_on_drive(zneni_frag_file)
    
    file_size_bytes = zneni_frag_file.stat().st_size
    file_size_gb = file_size_bytes / 1024 / 1024 / 1024
    logger.info(f"Building fragment index from: {zneni_frag_file}")
    logger.info(f"File size: {file_size_gb:.2f} GB")
    logger.info(f"Estimated time: {int(file_size_gb * 0.5)}-{int(file_size_gb * 1)} minutes (optimized)")
    
    # Load checkpoint if exists
    index: Dict[int, Dict] = {}
    doc_positions: Dict[str, int] = defaultdict(int)
    start_count = 0
    
    if checkpoint_file.exists():
        logger.info("Loading checkpoint...")
        try:
            with open(checkpoint_file, 'r', encoding='utf-8') as f:
                checkpoint = json.load(f)
            index = {int(k): v for k, v in checkpoint.get('index', {}).items()}
            doc_positions = defaultdict(int, {k: int(v) for k, v in checkpoint.get('doc_positions', {}).items()})
            start_count = checkpoint.get('count', 0)
            logger.info(f"Resuming from record {start_count:,}, {len(index):,} fragments indexed")
        except Exception as e:
            logger.warning(f"Failed to load checkpoint, starting fresh: {e}")
            index = {}
            doc_positions = defaultdict(int)
            start_count = 0
    
    count = 0
    skipped = 0
    
    # Estimate total records (rough: ~150 bytes per record average for this file)
    estimated_total = int(file_size_bytes / 150)
    
    try:
        with open(zneni_frag_file, 'rb') as f:
            items_iter = ijson.items(f, 'položky.item')
            
            for item in tqdm(
                items_iter, 
                desc="Indexing fragments",
                total=estimated_total,
                ncols=100,
                mininterval=1.0  # Update at most once per second
            ):
                count += 1
                
                # Skip already processed (for resume)
                if count <= start_count:
                    # Don't do anything expensive here - just skip
                    continue
                
                # Extract fragment info
                frag_info = item.get('právní-akt-fragment')
                if not frag_info:
                    skipped += 1
                    continue
                
                fragment_id = frag_info.get('fragment-id')
                if fragment_id is None:
                    skipped += 1
                    continue
                
                # Extract from URL/ELI
                url = item.get('znění-fragment-url') or ''
                eli = item.get('znění-fragment-eli') or ''
                hierarchy = item.get('znění-fragment-hierarchie') or ''
                
                # Parse document_id and effectiveness_date from URL
                document_id = ""
                effectiveness_date = ""
                
                if url:
                    clean_url = url.split('#')[0]
                    parts = clean_url.strip('/').split('/')
                    if len(parts) >= 3:
                        document_id = '/'.join(parts[:3])
                    if len(parts) >= 4:
                        date_part = parts[3]
                        # Valid date format, not placeholder
                        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_part) and date_part != "0000-00-00":
                            effectiveness_date = date_part
                
                # Fallback to ELI if URL didn't work
                if not document_id and eli:
                    match = re.search(r'/eli/cz/(\w+)/(\d+)/(\d+)(?:/(\d{4}-\d{2}-\d{2}))?', eli)
                    if match:
                        document_id = f"{match.group(1)}/{match.group(2)}/{match.group(3)}"
                        if match.group(4) and match.group(4) != "0000-00-00":
                            effectiveness_date = match.group(4)
                
                if not document_id:
                    skipped += 1
                    continue
                
                # Position within document
                doc_positions[document_id] += 1
                position = doc_positions[document_id]
                
                # Citation text
                section_citation = (
                    item.get('znění-fragment-citace-text') or
                    item.get('znění-fragment-označení-uzlu-text') or
                    ''
                )
                
                # Store
                index[fragment_id] = {
                    'document_id': document_id,
                    'eli': eli,
                    'hierarchy': hierarchy,
                    'section_citation': section_citation,
                    'position': position,
                    'effectiveness_date': effectiveness_date,
                }
                
                # Checkpoint periodically
                if count % batch_save_interval == 0:
                    logger.info(f"Checkpoint at {count:,} records, {len(index):,} fragments indexed")
                    _save_checkpoint(checkpoint_file, count, index, doc_positions)
    
    except KeyboardInterrupt:
        logger.warning("Interrupted! Saving checkpoint...")
        _save_checkpoint(checkpoint_file, count, index, doc_positions)
        raise
    except Exception as e:
        logger.error(f"Error at record {count}: {e}", exc_info=True)
        _save_checkpoint(checkpoint_file, count, index, doc_positions)
        raise
    
    logger.info(f"Indexing complete: {len(index):,} fragments from {count:,} records ({skipped:,} skipped)")
    logger.info(f"Found {len(doc_positions):,} unique documents")
    
    # Save final index
    logger.info(f"Saving index to: {index_file}")
    with open(index_file, 'w', encoding='utf-8') as f:
        # Convert int keys to strings for JSON
        json.dump({str(k): v for k, v in index.items()}, f)
    
    index_size_mb = index_file.stat().st_size / 1024 / 1024
    logger.info(f"Index saved: {index_size_mb:.1f} MB")
    
    # Clean up checkpoint
    if checkpoint_file.exists():
        checkpoint_file.unlink()
        logger.debug("Checkpoint file removed")
    
    return index


def _save_checkpoint(filepath: Path, count: int, index: Dict, doc_positions: Dict) -> None:
    """Save checkpoint to file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump({
            'count': count,
            'index': {str(k): v for k, v in index.items()},
            'doc_positions': dict(doc_positions),
        }, f)


def build_act_name_index(data_dir: Path, cache_dir: Path) -> Dict[str, str]:
    """
    Build index from 002PravniAkt.jsonld
    Maps: document_id (e.g., "sb/1918/8") -> act_name
    
    This is a smaller operation (~75MB file).
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    index_file = cache_dir / "act_name_index.json"
    
    if index_file.exists():
        size_kb = index_file.stat().st_size / 1024
        logger.info(f"Act name index already exists: {index_file} ({size_kb:.1f} KB)")
        with open(index_file, 'r', encoding='utf-8') as f:
            result = json.load(f)
        logger.info(f"Loaded {len(result):,} entries")
        return result
    
    akt_file = Path(data_dir) / "002PravniAkt.jsonld"
    if not akt_file.exists():
        logger.error(f"File not found: {akt_file}")
        return {}
    
    file_size_mb = akt_file.stat().st_size / 1024 / 1024
    logger.info(f"Building act name index from: {akt_file} ({file_size_mb:.1f} MB)")
    
    index: Dict[str, str] = {}
    count = 0
    
    # Estimate: ~500 bytes per record
    estimated_total = int(akt_file.stat().st_size / 500)
    
    with open(akt_file, 'rb') as f:
        for item in tqdm(
            ijson.items(f, 'položky.item'), 
            desc="Indexing act names",
            total=estimated_total,
            ncols=100
        ):
            count += 1
            
            iri = item.get('iri') or ''
            match = re.search(r'eli/cz/(\w+)/(\d+)/(\d+)', iri)
            if not match:
                continue
            
            document_id = f"{match.group(1)}/{match.group(2)}/{match.group(3)}"
            act_name = item.get('akt-název-vyhlášený') or ''
            
            if act_name:
                index[document_id] = act_name
    
    logger.info(f"Indexed {len(index):,} act names from {count:,} records")
    
    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=None)  # Compact format
    
    return index


if __name__ == "__main__":
    from config import setup_logging, setup_directories, DATA_DIR, CACHE_DIR, CHECKPOINT_DIR
    
    setup_directories()
    setup_logging("index_builder")
    
    # Build act name index first (smaller, faster)
    print("\n" + "="*50)
    print("Building act name index...")
    print("="*50)
    act_names = build_act_name_index(DATA_DIR, CACHE_DIR)
    print(f"✅ Act name index: {len(act_names):,} entries")
    
    # Build fragment index (large, takes time)
    print("\n" + "="*50)
    print("Building fragment index (this will take 30-60 minutes)...")
    print("="*50)
    fragments = build_fragment_index(DATA_DIR, CACHE_DIR, CHECKPOINT_DIR)
    print(f"✅ Fragment index: {len(fragments):,} entries")
