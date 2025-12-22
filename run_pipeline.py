"""
E-Sbírka Pipeline - Main Entry Point
Run this script to execute the full Phase 1 pipeline.

Usage in Colab:
    !python run_pipeline.py

With options:
    !python run_pipeline.py --skip-download --limit 1000
"""

import sys
import logging
from pathlib import Path

# Ensure we can import from current directory
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    setup_logging, setup_directories,
    DATA_DIR, CACHE_DIR, OUTPUT_DIR, CHECKPOINT_DIR,
    OPENDATA_BASE_URL, REQUIRED_FILES,
    FRAGMENT_BATCH_SAVE, MIN_TEXT_LENGTH
)


def run_pipeline(
    skip_download: bool = False,
    skip_index: bool = False,
    limit: int = None,
    batch_size: int = None,
) -> bool:
    """
    Run the complete Phase 1 pipeline.
    
    Args:
        skip_download: Skip downloading OpenData files (use existing)
        skip_index: Skip building indexes (use cached)
        limit: Limit number of fragments to process (for testing)
        batch_size: Override batch save size
        
    Returns:
        True if successful, False otherwise
    """
    logger = logging.getLogger(__name__)
    
    # ========================================================================
    # STEP 1: Download OpenData
    # ========================================================================
    if not skip_download:
        logger.info("=" * 60)
        logger.info("STEP 1: DOWNLOADING OPENDATA FILES")
        logger.info("=" * 60)
        
        from downloader import download_opendata
        
        results = download_opendata(DATA_DIR, OPENDATA_BASE_URL, REQUIRED_FILES)
        
        failed = [k for k, v in results.items() if not v]
        if failed:
            logger.error(f"Download failed for: {failed}")
            logger.error("Cannot continue without all required files.")
            return False
        
        logger.info("✅ All files downloaded successfully")
    else:
        logger.info("Skipping download (--skip-download flag)")
        # Verify files exist
        missing = []
        for f in REQUIRED_FILES:
            path = DATA_DIR / f"{f}.jsonld"
            if not path.exists():
                missing.append(f)
        if missing:
            logger.error(f"Missing required files: {missing}")
            logger.error("Cannot skip download - files don't exist")
            return False
    
    # ========================================================================
    # STEP 2: Build indexes
    # ========================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("STEP 2: BUILDING INDEXES")
    logger.info("=" * 60)
    
    from index_builder import build_fragment_index, build_act_name_index
    
    logger.info("Loading act name index...")
    act_name_index = build_act_name_index(DATA_DIR, CACHE_DIR)
    logger.info(f"✅ Act names: {len(act_name_index):,} entries")
    
    logger.info("")
    logger.info("Loading fragment index (this may take 30-60 minutes if building from scratch)...")
    fragment_index = build_fragment_index(DATA_DIR, CACHE_DIR, CHECKPOINT_DIR)
    logger.info(f"✅ Fragments: {len(fragment_index):,} entries")
    
    if len(fragment_index) == 0:
        logger.error("Fragment index is empty - cannot proceed")
        return False
    
    # ========================================================================
    # STEP 3: Process fragments
    # ========================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("STEP 3: PROCESSING FRAGMENTS INTO JSON FILES")
    logger.info("=" * 60)
    
    from fragment_processor import FragmentProcessor
    
    processor = FragmentProcessor(
        data_dir=DATA_DIR,
        output_dir=OUTPUT_DIR,
        checkpoint_dir=CHECKPOINT_DIR,
        fragment_index=fragment_index,
        act_name_index=act_name_index,
        current_only=True,
        min_text_length=MIN_TEXT_LENGTH,
    )
    
    processor.process(
        limit=limit, 
        batch_save_every=batch_size or FRAGMENT_BATCH_SAVE
    )
    
    # ========================================================================
    # STEP 4: Build manifest
    # ========================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("STEP 4: BUILDING MANIFEST")
    logger.info("=" * 60)
    
    manifest = processor.build_manifest()
    
    # ========================================================================
    # Summary
    # ========================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ PHASE 1 COMPLETE!")
    logger.info("=" * 60)
    logger.info(f"Total acts: {manifest['stats']['total_acts']:,}")
    logger.info(f"Total fragments: {manifest['stats']['total_fragments']:,}")
    logger.info(f"Output directory: {OUTPUT_DIR}")
    logger.info(f"Manifest: {OUTPUT_DIR / '_sync' / 'manifest.json'}")
    
    return True


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="E-Sbírka Phase 1 Pipeline - Build JSON files from OpenData",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pipeline.py                    # Full run
  python run_pipeline.py --limit 1000       # Test with 1000 fragments
  python run_pipeline.py --skip-download    # Use existing data files
        """
    )
    parser.add_argument(
        '--skip-download', 
        action='store_true', 
        help="Skip downloading files (use existing)"
    )
    parser.add_argument(
        '--skip-index', 
        action='store_true', 
        help="Skip index building (use cached)"
    )
    parser.add_argument(
        '--limit', 
        type=int, 
        default=None, 
        help="Limit number of fragments to process (for testing)"
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=None,
        help="Number of acts to buffer before saving (default: 500)"
    )
    
    args = parser.parse_args()
    
    # Setup
    setup_directories()
    setup_logging("pipeline")
    
    # Run
    try:
        success = run_pipeline(
            skip_download=args.skip_download,
            skip_index=args.skip_index,
            limit=args.limit,
            batch_size=args.batch_size,
        )
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Progress has been saved.")
        print("Run again to resume from checkpoint.")
        sys.exit(130)
    except Exception as e:
        logging.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
