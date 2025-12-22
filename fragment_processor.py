"""
Fragment Processor
Processes 004PravniAktFragment and creates per-act JSON files.
Optimized for large files with checkpointing and resume support.
"""

import ijson
import json
import re
import gc
import hashlib
import logging
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Set
from tqdm.auto import tqdm
from collections import defaultdict
from html.parser import HTMLParser
from io import StringIO

logger = logging.getLogger(__name__)


# ============================================================================
# HTML STRIPPING (using stdlib - no external dependencies)
# ============================================================================

class _MLStripper(HTMLParser):
    """HTML tag stripper using stdlib."""
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text = StringIO()
    
    def handle_data(self, d):
        self.text.write(d)
    
    def get_data(self):
        return self.text.getvalue()


def strip_html(html: str) -> str:
    """
    Remove HTML tags and normalize whitespace.
    Uses stdlib HTMLParser for reliability.
    """
    if not html:
        return ""
    try:
        s = _MLStripper()
        s.feed(html)
        text = s.get_data()
    except Exception:
        # Fallback to regex if parser fails
        text = re.sub(r'<[^>]+>', ' ', html)
    
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def generate_chunk_id(document_id: str, fragment_id: int, text: str) -> str:
    """Generate unique chunk ID using MD5 hash."""
    content = f"{document_id}:{fragment_id}:{text[:100]}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def is_current_version(eff_from: str, eff_to: Optional[str], version_type: str = "KONSOL") -> bool:
    """
    Determine if version is currently effective.
    
    CRITICAL LOGIC:
    - Must be KONSOL type (not VYHLZNE - published versions always have null end date)
    - effectiveness_from must be <= today
    - effectiveness_to must be None or >= today
    """
    if version_type != "KONSOL":
        return False
    
    today_str = date.today().isoformat()
    
    # Must have a start date in the past or today
    if not eff_from or eff_from > today_str:
        return False
    
    # End date must be None (ongoing) or in the future
    if eff_to and eff_to < today_str:
        return False
    
    return True


def build_kod_dokumentu(document_id: str) -> str:
    """
    Convert document_id to formatted citation.
    Example: 'sb/2006/262' -> '262/2006 Sb.'
    """
    parts = document_id.split('/')
    if len(parts) >= 3:
        sbirka_map = {
            'sb': 'Sb.',
            'ul': 'Ul.',
            'ul1': 'Ul.',
            'mmk': 'MMK',
        }
        sbirka = sbirka_map.get(parts[0].lower(), parts[0].upper() + '.')
        return f"{parts[2]}/{parts[1]} {sbirka}"
    return document_id


# ============================================================================
# MAIN PROCESSOR CLASS
# ============================================================================

class FragmentProcessor:
    """
    Process fragments from 004PravniAktFragment and create per-act JSON files.
    
    Features:
    - Checkpoint/resume support
    - Memory-efficient streaming
    - Tracks saved acts to avoid duplication on resume
    """
    
    def __init__(
        self,
        data_dir: Path,
        output_dir: Path,
        checkpoint_dir: Path,
        fragment_index: Dict[int, Dict],
        act_name_index: Dict[str, str],
        current_only: bool = True,
        min_text_length: int = 10,
    ):
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.acts_dir = self.output_dir / 'acts'
        self.sync_dir = self.output_dir / '_sync'
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_file = self.checkpoint_dir / 'processor_checkpoint.json'
        
        self.fragment_index = fragment_index
        self.act_name_index = act_name_index
        self.current_only = current_only
        self.min_text_length = min_text_length
        
        # Ensure directories
        self.acts_dir.mkdir(parents=True, exist_ok=True)
        self.sync_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        
        # Buffers and state
        self.act_buffers: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))
        self.saved_acts: Set[str] = set()
        self.stats = {
            'processed': 0,
            'skipped_no_text': 0,
            'skipped_no_index': 0,
            'skipped_not_current': 0,
            'skipped_already_saved': 0,
            'acts_saved': 0,
            'errors': 0,
        }
    
    def load_checkpoint(self) -> int:
        """Load processing checkpoint. Returns last processed count."""
        if not self.checkpoint_file.exists():
            return 0
        
        try:
            with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                cp = json.load(f)
            
            self.stats = cp.get('stats', self.stats)
            self.saved_acts = set(cp.get('saved_acts', []))
            last_count = cp.get('last_count', 0)
            
            logger.info(f"Checkpoint loaded: {last_count:,} records, {len(self.saved_acts):,} acts already saved")
            return last_count
            
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}")
            return 0
    
    def save_checkpoint(self, count: int) -> None:
        """Save processing checkpoint."""
        try:
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'last_count': count,
                    'stats': self.stats,
                    'saved_acts': list(self.saved_acts),
                    'timestamp': datetime.now().isoformat(),
                }, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
    
    def save_act(self, act_id: str) -> bool:
        """
        Save a single act to JSON file.
        Returns True if saved successfully.
        """
        if act_id not in self.act_buffers:
            return False
        
        versions_data = self.act_buffers[act_id]
        if not versions_data:
            del self.act_buffers[act_id]
            return False
        
        # Build versions list (newest first)
        versions = []
        for eff_date, fragments in sorted(versions_data.items(), key=lambda x: x[0] or '', reverse=True):
            if not fragments:
                continue
            
            is_current = is_current_version(eff_date, None, "KONSOL") if eff_date else False
            
            version = {
                'version_id': f"{act_id}/{eff_date}" if eff_date else f"{act_id}/unknown",
                'stale_url': f"/{act_id}/{eff_date}" if eff_date else f"/{act_id}",
                'version_type_opendata': 'KONSOL',
                'version_type_api': None,
                'effectiveness_from': eff_date if eff_date else None,
                'effectiveness_to': None,
                'is_current': is_current,
                'last_api_change': None,
                'processed_at': datetime.now().isoformat(),
                'fragment_count': len(fragments),
                'fragments': fragments,
            }
            versions.append(version)
        
        if not versions:
            del self.act_buffers[act_id]
            return False
        
        # Build act object
        act = {
            'act_id': act_id,
            'kod_dokumentu_sbirky': build_kod_dokumentu(act_id),
            'act_name': self.act_name_index.get(act_id, ''),
            'created_at': datetime.now().isoformat(),
            'last_modified': datetime.now().isoformat(),
            'versions': versions,
        }
        
        # Save file
        filename = act_id.replace('/', '_') + '.json'
        filepath = self.acts_dir / filename
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(act, f, ensure_ascii=False, indent=2)
            
            self.stats['acts_saved'] += 1
            self.saved_acts.add(act_id)
            del self.act_buffers[act_id]
            return True
            
        except Exception as e:
            logger.error(f"Failed to save act {act_id}: {e}")
            self.stats['errors'] += 1
            return False
    
    def process(self, limit: Optional[int] = None, batch_save_every: int = 500) -> None:
        """
        Main processing loop.
        
        Args:
            limit: Max fragments to process (None = all)
            batch_save_every: Flush buffer and save acts when buffer has this many unique acts
        """
        fragment_file = self.data_dir / "004PravniAktFragment.jsonld"
        if not fragment_file.exists():
            logger.error(f"File not found: {fragment_file}")
            return
        
        file_size_bytes = fragment_file.stat().st_size
        file_size_gb = file_size_bytes / 1024 / 1024 / 1024
        
        logger.info(f"Processing fragments from: {fragment_file}")
        logger.info(f"File size: {file_size_gb:.2f} GB")
        
        # Load checkpoint
        resume_from = self.load_checkpoint()
        if resume_from > 0:
            logger.info(f"Resuming from record {resume_from:,}")
        
        if limit:
            logger.info(f"Limit: {limit:,} fragments")
        logger.info(f"Mode: {'Current versions only' if self.current_only else 'All versions'}")
        logger.info(f"Batch save every: {batch_save_every} acts")
        
        count = 0
        processed_since_checkpoint = 0
        
        # Estimate: ~100 bytes per record (5.9GB / ~60M records)
        estimated_total = int(file_size_bytes / 100)
        
        try:
            with open(fragment_file, 'rb') as f:
                for item in tqdm(
                    ijson.items(f, 'položky.item'), 
                    desc="Processing",
                    total=estimated_total,
                    ncols=100,
                    mininterval=1.0
                ):
                    count += 1
                    
                    # Skip to resume point (fast skip)
                    if count <= resume_from:
                        continue
                    
                    # Check limit
                    if limit and processed_since_checkpoint >= limit:
                        logger.info(f"Reached limit of {limit:,}")
                        break
                    
                    processed_since_checkpoint += 1
                    
                    # Get fragment text
                    html_text = item.get('fragment-text') or ''
                    text = strip_html(html_text)
                    
                    if not text or len(text) < self.min_text_length:
                        self.stats['skipped_no_text'] += 1
                        continue
                    
                    # Get fragment ID
                    fragment_id = item.get('fragment-id')
                    if fragment_id is None:
                        self.stats['skipped_no_index'] += 1
                        continue
                    
                    # Look up in index
                    index_entry = self.fragment_index.get(fragment_id)
                    if not index_entry:
                        self.stats['skipped_no_index'] += 1
                        continue
                    
                    document_id = index_entry.get('document_id', '')
                    eff_date = index_entry.get('effectiveness_date', '')
                    section_citation = index_entry.get('section_citation', '')
                    position = index_entry.get('position', 0)
                    
                    # Skip already-saved acts (for resume correctness)
                    if document_id in self.saved_acts:
                        self.stats['skipped_already_saved'] += 1
                        continue
                    
                    # Filter non-current versions
                    if self.current_only:
                        if not is_current_version(eff_date, None, 'KONSOL'):
                            self.stats['skipped_not_current'] += 1
                            continue
                    
                    # Build fragment record
                    chunk_id = generate_chunk_id(document_id, fragment_id, text)
                    
                    fragment_record = {
                        'chunk_id': chunk_id,
                        'fragment_id': fragment_id,
                        'section_citation': section_citation,
                        'hierarchy_path': f"{build_kod_dokumentu(document_id)} > {section_citation}" if section_citation else build_kod_dokumentu(document_id),
                        'chunk_text': text,
                        'position': position,
                        'char_count': len(text),
                    }
                    
                    # Add to buffer
                    self.act_buffers[document_id][eff_date].append(fragment_record)
                    self.stats['processed'] += 1
                    
                    # Periodic save when buffer is large enough
                    if len(self.act_buffers) >= batch_save_every:
                        logger.info(f"Saving batch of {len(self.act_buffers)} acts at record {count:,}...")
                        for aid in list(self.act_buffers.keys()):
                            self.save_act(aid)
                        self.save_checkpoint(count)
                        # Free memory
                        gc.collect()
        
        except KeyboardInterrupt:
            logger.warning("Interrupted! Saving progress...")
            for aid in list(self.act_buffers.keys()):
                self.save_act(aid)
            self.save_checkpoint(count)
            logger.info(f"Checkpoint saved at record {count:,}. Resume will continue from here.")
            raise
        
        except Exception as e:
            logger.error(f"Error at record {count}: {e}", exc_info=True)
            self.stats['errors'] += 1
            # Save what we have
            for aid in list(self.act_buffers.keys()):
                self.save_act(aid)
            self.save_checkpoint(count)
            raise
        
        # Save remaining buffered acts
        remaining = len(self.act_buffers)
        if remaining > 0:
            logger.info(f"Saving final {remaining} acts...")
            for aid in list(self.act_buffers.keys()):
                self.save_act(aid)
        
        self.save_checkpoint(count)
        
        # Log final stats
        logger.info("=" * 50)
        logger.info("PROCESSING COMPLETE")
        logger.info("=" * 50)
        for k, v in self.stats.items():
            logger.info(f"  {k}: {v:,}")
    
    def build_manifest(self) -> Dict[str, Any]:
        """Build manifest.json from saved act files."""
        logger.info("Building manifest from saved act files...")
        
        manifest: Dict[str, Any] = {
            'version': 1,
            'created_at': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'stats': {
                'total_acts': 0,
                'total_versions': 0,
                'total_fragments': 0,
            },
            'acts': {},
        }
        
        act_files = list(self.acts_dir.glob('*.json'))
        logger.info(f"Found {len(act_files):,} act files")
        
        errors = 0
        for act_file in tqdm(act_files, desc="Building manifest", ncols=100):
            try:
                with open(act_file, 'r', encoding='utf-8') as f:
                    act = json.load(f)
                
                act_id = act['act_id']
                versions = act.get('versions', [])
                total_frags = sum(v.get('fragment_count', 0) for v in versions)
                
                # Find current version
                current_version = None
                for v in versions:
                    if v.get('is_current'):
                        current_version = v.get('version_id')
                        break
                
                manifest['acts'][act_id] = {
                    'kod_dokumentu_sbirky': act.get('kod_dokumentu_sbirky', ''),
                    'act_name': act.get('act_name', ''),
                    'file_path': f"acts/{act_file.name}",
                    'current_version': current_version,
                    'fragment_count': total_frags,
                    'processed_at': act.get('last_modified', ''),
                }
                
                manifest['stats']['total_acts'] += 1
                manifest['stats']['total_versions'] += len(versions)
                manifest['stats']['total_fragments'] += total_frags
                
            except Exception as e:
                logger.error(f"Error processing {act_file.name}: {e}")
                errors += 1
        
        if errors > 0:
            logger.warning(f"Encountered {errors} errors while building manifest")
        
        # Save manifest
        manifest_file = self.sync_dir / 'manifest.json'
        with open(manifest_file, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Manifest saved: {manifest_file}")
        logger.info(f"  Total acts: {manifest['stats']['total_acts']:,}")
        logger.info(f"  Total versions: {manifest['stats']['total_versions']:,}")
        logger.info(f"  Total fragments: {manifest['stats']['total_fragments']:,}")
        
        # Save sync state
        sync_state = {
            'last_full_build': datetime.now().isoformat(),
            'last_api_sync': None,
            'opendata_source_date': date.today().isoformat(),
        }
        with open(self.sync_dir / 'sync_state.json', 'w', encoding='utf-8') as f:
            json.dump(sync_state, f, indent=2)
        
        return manifest


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    from config import (
        setup_logging, setup_directories, 
        DATA_DIR, CACHE_DIR, OUTPUT_DIR, CHECKPOINT_DIR,
        FRAGMENT_BATCH_SAVE, MIN_TEXT_LENGTH
    )
    from index_builder import build_fragment_index, build_act_name_index
    
    setup_directories()
    setup_logging("fragment_processor")
    
    # Load indexes
    print("\n" + "="*50)
    print("Loading indexes...")
    print("="*50)
    
    fragment_index = build_fragment_index(DATA_DIR, CACHE_DIR, CHECKPOINT_DIR)
    act_name_index = build_act_name_index(DATA_DIR, CACHE_DIR)
    
    print(f"Fragment index: {len(fragment_index):,} entries")
    print(f"Act name index: {len(act_name_index):,} entries")
    
    # Process
    print("\n" + "="*50)
    print("Processing fragments...")
    print("="*50)
    
    processor = FragmentProcessor(
        data_dir=DATA_DIR,
        output_dir=OUTPUT_DIR,
        checkpoint_dir=CHECKPOINT_DIR,
        fragment_index=fragment_index,
        act_name_index=act_name_index,
        current_only=True,
        min_text_length=MIN_TEXT_LENGTH,
    )
    
    processor.process(limit=None, batch_save_every=FRAGMENT_BATCH_SAVE)
    
    # Build manifest
    print("\n" + "="*50)
    print("Building manifest...")
    print("="*50)
    
    processor.build_manifest()
    
    print("\n" + "="*50)
    print("✅ PHASE 1 COMPLETE!")
    print("="*50)
