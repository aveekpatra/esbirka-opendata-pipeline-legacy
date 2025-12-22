"""
E-Sbírka OpenData Downloader
Downloads and extracts all necessary OpenData files with resume support.
"""

import gzip
import logging
import requests
from pathlib import Path
from tqdm.auto import tqdm
from typing import Dict, List

logger = logging.getLogger(__name__)


def download_file(
    url: str, 
    dest_path: Path, 
    chunk_size: int = 32768,
    timeout: int = 300
) -> bool:
    """
    Download a file with progress bar and resume support.
    
    Args:
        url: URL to download from
        dest_path: Destination file path
        chunk_size: Download chunk size (default 32KB)
        timeout: Request timeout in seconds
        
    Returns:
        True if successful, False otherwise
    """
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Check for existing partial download
        resume_pos = 0
        if dest_path.exists():
            resume_pos = dest_path.stat().st_size
        
        # First, get file size with HEAD request
        try:
            head_resp = requests.head(url, timeout=30)
            head_resp.raise_for_status()
            total_size = int(head_resp.headers.get('content-length', 0))
        except Exception:
            total_size = 0  # Unknown size
        
        # Check if already complete
        if resume_pos > 0 and total_size > 0 and resume_pos >= total_size:
            logger.info(f"File already complete: {dest_path.name} ({resume_pos:,} bytes)")
            return True
        
        headers = {}
        if resume_pos > 0:
            headers['Range'] = f'bytes={resume_pos}-'
            logger.info(f"Resuming download from byte {resume_pos:,}")
        
        response = requests.get(url, headers=headers, stream=True, timeout=timeout)
        
        # 416 = Range not satisfiable = file already complete
        if response.status_code == 416:
            logger.info(f"File already complete: {dest_path.name}")
            return True
        
        response.raise_for_status()
        
        # Get remaining size from response (for resumed downloads)
        content_length = int(response.headers.get('content-length', 0))
        if total_size == 0:
            total_size = content_length + resume_pos
        
        mode = 'ab' if resume_pos > 0 else 'wb'
        
        with open(dest_path, mode) as f:
            with tqdm(
                total=total_size, 
                initial=resume_pos,
                unit='B', 
                unit_scale=True,
                unit_divisor=1024,
                desc=dest_path.name[:30],
                ncols=80,
                leave=True
            ) as pbar:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
        
        # Verify download
        final_size = dest_path.stat().st_size
        if total_size > 0 and final_size < total_size:
            logger.warning(f"Incomplete download: {final_size:,}/{total_size:,} bytes")
            return False
        
        logger.info(f"Downloaded: {dest_path.name} ({final_size:,} bytes)")
        return True
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout downloading: {url}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Download failed: {url} -> {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error downloading {url}: {e}", exc_info=True)
        return False


def decompress_gzip(gz_path: Path, out_path: Path, delete_source: bool = True) -> bool:
    """
    Decompress a .gz file with progress bar.
    
    Args:
        gz_path: Path to .gz file
        out_path: Output path for decompressed file
        delete_source: Delete .gz after decompression to save space
        
    Returns:
        True if successful
    """
    try:
        if not gz_path.exists():
            logger.error(f"Compressed file not found: {gz_path}")
            return False
        
        gz_size = gz_path.stat().st_size
        logger.info(f"Decompressing: {gz_path.name} ({gz_size / 1024 / 1024:.1f} MB compressed)")
        
        bytes_read = 0
        with gzip.open(gz_path, 'rb') as f_in:
            with open(out_path, 'wb') as f_out:
                with tqdm(
                    total=gz_size,
                    unit='B',
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=f"Extracting",
                    ncols=80,
                    leave=True
                ) as pbar:
                    while True:
                        # Read decompressed data
                        chunk = f_in.read(65536)  # 64KB chunks
                        if not chunk:
                            break
                        f_out.write(chunk)
                        
                        # Update progress based on compressed position
                        current_pos = f_in.fileobj.tell()
                        delta = current_pos - bytes_read
                        bytes_read = current_pos
                        pbar.update(delta)
        
        out_size = out_path.stat().st_size
        ratio = out_size / gz_size if gz_size > 0 else 0
        logger.info(f"Decompressed: {out_path.name} ({out_size / 1024 / 1024:.1f} MB, ratio: {ratio:.1f}x)")
        
        # Delete compressed file to save space
        if delete_source and out_path.exists() and out_size > 0:
            gz_path.unlink()
            logger.debug(f"Deleted compressed file: {gz_path.name}")
        
        return True
        
    except Exception as e:
        logger.error(f"Decompression failed: {e}", exc_info=True)
        # Clean up partial output
        if out_path.exists():
            out_path.unlink()
        return False


def download_opendata(
    data_dir: Path, 
    base_url: str,
    files: List[str],
) -> Dict[str, bool]:
    """
    Download and extract OpenData files.
    
    Args:
        data_dir: Directory to save files
        base_url: Base URL for OpenData
        files: List of file names (without extension)
        
    Returns:
        Dict mapping filename to success status
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    
    logger.info(f"Downloading {len(files)} files to: {data_dir}")
    
    for i, filename in enumerate(files, 1):
        logger.info(f"\n[{i}/{len(files)}] Processing: {filename}")
        
        jsonld_path = data_dir / f"{filename}.jsonld"
        gz_path = data_dir / f"{filename}.jsonld.gz"
        
        # Skip if already extracted and valid
        if jsonld_path.exists():
            size = jsonld_path.stat().st_size
            if size > 100:  # More than 100 bytes = probably valid
                logger.info(f"✓ Already exists: {filename}.jsonld ({size / 1024 / 1024:.1f} MB)")
                results[filename] = True
                continue
            else:
                logger.warning(f"File exists but too small ({size} bytes), re-downloading...")
                jsonld_path.unlink()
        
        # Download compressed file
        url = f"{base_url}{filename}.jsonld.gz"
        
        if not gz_path.exists():
            logger.info(f"Downloading: {url}")
            if not download_file(url, gz_path):
                results[filename] = False
                logger.error(f"✗ Failed to download: {filename}")
                continue
        else:
            logger.info(f"Using existing compressed file: {gz_path.name}")
        
        # Decompress
        if decompress_gzip(gz_path, jsonld_path):
            results[filename] = True
            logger.info(f"✓ Complete: {filename}")
        else:
            results[filename] = False
            logger.error(f"✗ Failed to decompress: {filename}")
    
    return results


if __name__ == "__main__":
    from config import (
        setup_logging, setup_directories, 
        DATA_DIR, OPENDATA_BASE_URL, REQUIRED_FILES
    )
    
    setup_directories()
    setup_logging("downloader")
    
    results = download_opendata(DATA_DIR, OPENDATA_BASE_URL, REQUIRED_FILES)
    
    # Summary
    success = sum(1 for v in results.values() if v)
    total = len(results)
    
    print(f"\n{'='*50}")
    print(f"Download complete: {success}/{total} files")
    
    if success == total:
        print("✅ All files downloaded successfully!")
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"❌ Failed: {failed}")
