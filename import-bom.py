import argparse
import os
import sys
import tempfile
import subprocess
import requests
from typing import List, Optional, Dict

# Attempt to import BeautifulSoup, needed for web parsing/scraping
try:
    from bs4 import BeautifulSoup
except ImportError:
    # This error will be raised clearly in the __main__ block if not found
    BeautifulSoup = None

# --- Configuration ---
# NOTE: Replace 'prusa-slicer' with the full path to the executable if it is not
# in your system's PATH (e.g., 'C:\Program Files\PrusaSlicer\prusa-slicer.exe'
# on Windows or '/Applications/PrusaSlicer.app/Contents/MacOS/prusa-slicer' on macOS).
PRUSA_SLICER_COMMAND = 'prusa-slicer'

def get_thangs_download_url(search_url: str) -> Optional[str]:
    """
    Fetches a Thangs search URL, navigates to the first model page, and attempts
    to extract the direct STL download link.

    NOTE: This relies on the current HTML structure of Thangs, which is subject
    to frequent change and may not work if content is loaded via JavaScript.

    Args:
        search_url: The initial Thangs search or model page URL.

    Returns:
        The direct link to the downloadable file (STL/3MF/etc.) or None on failure.
    """
    if BeautifulSoup is None:
        print("    [Thangs Scraper] BeautifulSoup library not found. Cannot process Thangs URLs.", file=sys.stderr)
        return None

    base_url = "https://thangs.com"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    model_url = search_url

    # --- 1. Resolve Search URL to Model Page URL (If necessary) ---
    if "/search/" in search_url:
        try:
            print(f"    [Thangs Scraper] Fetching search page to find model link...")
            response = requests.get(search_url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            # This selector attempts to find the link to the first model result.
            # This is highly prone to breaking if Thangs updates their site.
            first_model_link = soup.select_one('a[href*="/3d-model/"]')

            if first_model_link and first_model_link.get('href'):
                model_path = first_model_link['href']
                model_url = base_url + model_path
                print(f"    [Thangs Scraper] Found model page link: {model_url}")
            else:
                print("    [Thangs Scraper] Could not find a model link on the search results page. Trying search URL as model URL.")
                # Fallback: assume the search page might redirect or contain the download element itself (unlikely)

        except Exception as e:
            print(f"    [Thangs Scraper] ERROR: Failed to parse search URL or find model link: {e}", file=sys.stderr)
            return None

    # --- 2. Fetch Model Page and Extract Download Link ---
    try:
        print(f"    [Thangs Scraper] Fetching model page to find download URL...")
        response = requests.get(model_url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # This selector attempts to find the download button/link element.
        # This is the most brittle part of the scraping process.
        download_button = soup.select_one('a[data-testid="download-file-button"]')

        if not download_button:
             # Try a more generic link that often leads to the download process
            download_button = soup.select_one('a[href*="/download/"]')


        if download_button and download_button.get('href'):
            # Thangs download links can be relative, so we ensure they are absolute
            download_path = download_button['href']
            if download_path.startswith('/'):
                 final_download_url = base_url + download_path
            else:
                 final_download_url = download_path # Already absolute

            print(f"    [Thangs Scraper] Found final download URL: {final_download_url}")
            return final_download_url

        print("    [Thangs Scraper] ERROR: Could not find the direct download link on the model page.")
        return None

    except Exception as e:
        print(f"    [Thangs Scraper] ERROR: Failed to fetch model page or extract download link: {e}", file=sys.stderr)
        return None

def download_files(urls: List[str], target_dir: str) -> Dict[str, str]:
    """
    Downloads 3D files from a list of unique URLs into the specified temporary directory.

    Args:
        urls: A list of unique strings, each being a URL to an STL file or a Thangs page.
        target_dir: The path to the directory where files will be saved.

    Returns:
        A dictionary mapping the original URL to the local file path of the downloaded file.
    """
    url_map = {}
    print(f"Starting downloads to temporary directory: {target_dir}")

    for i, url in enumerate(urls):
        original_url = url # Keep the original for the map key
        download_url = url

        # --- Thangs URL Handling ---
        if 'thangs.com' in url.lower():
            print(f"  Attempting to resolve Thangs URL: {original_url}")
            resolved_url = get_thangs_download_url(url)
            if resolved_url:
                download_url = resolved_url
            else:
                print(f"  WARNING: Could not resolve Thangs URL {original_url}. Skipping.", file=sys.stderr)
                continue
        # --- END Thangs Handling ---

        try:
            print(f"  Downloading file {i+1}/{len(urls)}: {download_url}...")
            # Use streaming to handle potentially large files
            # Add headers to mimic a browser request, which helps prevent 403 errors
            headers = {'User-Agent': 'Mozilla/5.0'}
            with requests.get(download_url, stream=True, allow_redirects=True, timeout=30, headers=headers) as r:
                r.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

                # Try to determine the filename from headers or default to index
                filename = download_url.split('/')[-1]
                if not filename or '.' not in filename or len(filename) > 50:
                    # Fallback filename if URL doesn't provide a clear name or is too long
                    filename = f"model_{i+1}.stl"

                # Clean filename of query params if present
                if '?' in filename:
                    filename = filename.split('?')[0]

                # Ensure the filename ends with a 3D model extension (PrusaSlicer requirement)
                if not filename.lower().endswith(('.stl', '.amf', '.obj', '.3mf')):
                     filename = os.path.splitext(filename)[0] + '.stl'

                local_path = os.path.join(target_dir, filename)

                # Write the file content in chunks
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

                url_map[original_url] = local_path
                print(f"  Successfully saved to: {local_path}")

        except requests.exceptions.RequestException as e:
            print(f"  ERROR: Failed to download {url}. Reason: {e}", file=sys.stderr)
        except Exception as e:
            print(f"  An unexpected error occurred during download: {e}", file=sys.stderr)

    return url_map

def run_prusa_slicer(stl_files: List[str], output_path: str):
    """
    Executes the prusa-slicer command with the downloaded files and export flag.

    Args:
        stl_files: A list of local paths to the 3D files.
        output_path: The desired path for the final 3MF output file.
    """
    if not stl_files:
        print("No files (local or remote) were found to process. Skipping PrusaSlicer execution.")
        return

    # Construct the command line argument list
    command = [
        PRUSA_SLICER_COMMAND,
        '--export-3mf',
        output_path
    ]

    # Add all file paths (both downloaded and local)
    command.extend(stl_files)

    print("\n--- Running PrusaSlicer Command ---")
    # Truncate output if it's extremely long due to many duplicated files
    cmd_str = " ".join(command)
    if len(cmd_str) > 1000:
        print(cmd_str[:1000] + " ... [command truncated]")
    else:
        print(cmd_str)

    try:
        # Execute the command
        # PrusaSlicer is often a GUI application, so capturing output might block.
        # We rely on the return code for success/failure.
        result = subprocess.run(command, check=True, capture_output=True, text=True)

        print("\nPrusaSlicer execution successful.")
        print(f"3MF file created at: {os.path.abspath(output_path)}")

        # Optional: Print captured output if any (might be empty for GUI apps)
        if result.stdout:
            print("\nPrusaSlicer Output (stdout):")
            print(result.stdout)
        if result.stderr:
            print("\nPrusaSlicer Output (stderr):")
            print(result.stderr)

    except subprocess.CalledProcessError as e:
        print(f"\nERROR: PrusaSlicer exited with a non-zero status code {e.returncode}.", file=sys.stderr)
        print("PrusaSlicer Error Output (stderr):", file=sys.stderr)
        print(e.stderr, file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"\nERROR: PrusaSlicer executable '{PRUSA_SLICER_COMMAND}' not found.", file=sys.stderr)
        print("Please ensure PrusaSlicer is installed and accessible in your system's PATH, or update the PRUSA_SLICER_COMMAND variable in the script.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred during PrusaSlicer execution: {e}", file=sys.stderr)
        sys.exit(1)

def parse_input_file(filepath: str) -> List[dict]:
    """
    Parses the input file. Supports simple URL lists and BOM (Bill of Materials) formats.
    Returns a list of dicts: {'source': str, 'quantity': int}
    """
    items = []
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(('#', '-')) or "Part Name" in line:
                    continue

                # Check for BOM format: "Quantity | Part Name | URL"
                # Example: "4 | 1 MU L ... | https://..."
                if '|' in line:
                    parts = [p.strip() for p in line.split('|')]
                    # Expecting at least 3 columns for a valid BOM line with URL
                    if len(parts) >= 3:
                        try:
                            # Parse Quantity
                            qty_str = parts[0]
                            # Handle cases where quantity might be "1x" or similar, though "4" is standard
                            qty = int(re.search(r'\d+', qty_str).group()) if re.search(r'\d+', qty_str) else 1

                            # Column index 2 is usually the URL in the provided format
                            source = parts[2]
                            if source.lower().startswith(('http', 'ftp')):
                                items.append({'source': source, 'quantity': qty})
                            else:
                                print(f"  Skipping BOM line (invalid URL in col 3): {line}", file=sys.stderr)
                        except (ValueError, IndexError):
                             print(f"  Skipping BOM line (parsing error): {line}", file=sys.stderr)
                    else:
                        # Handle cases with no URL (length 2 or less)
                        print(f"  Skipping BOM line (missing URL): {line}", file=sys.stderr)
                else:
                    # Simple list format: "URL" or "path"
                    # Default quantity is 1
                    items.append({'source': line, 'quantity': 1})
    except Exception as e:
        print(f"Error reading input file: {e}", file=sys.stderr)
        sys.exit(1)

    return items

def main():
    """Main function to parse arguments and run the workflow."""
    parser = argparse.ArgumentParser(
        description="Download 3D files from a list of URLs and local paths, and combine them into a 3MF file using PrusaSlicer.",
        epilog="""
Example Input File Format (input.txt):
--------------------------------------
# Simple Format
https://example.com/files/bracket.stl
/Users/name/Documents/3d_models/base.obj

# BOM Format (Pipe Separated)
# Quantity | Part Name | URL
4 | Bracket Left | https://thangs.com/...
2 | Base Plate   | https://example.com/base.stl

Usage Example:
--------------
python prusa_batch_processor.py input_urls.txt my_project.3mf
""",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        'url_input_file',
        type=str,
        help="Path to a text file containing URLs, local paths, or a BOM table."
    )
    parser.add_argument(
        'output_3mf_file',
        type=str,
        help="The filename for the resulting 3MF project file (e.g., 'my_project.3mf')."
    )

    args = parser.parse_args()

    # Needs regex for parse_input_file
    global re
    import re

    # 1. Parse Input
    print(f"Parsing input file: {args.url_input_file}")
    items = parse_input_file(args.url_input_file)

    if not items:
        print(f"Input file '{args.url_input_file}' contains no valid items to process.")
        return

    # 2. Separate into unique remote URLs and local files
    unique_remote_urls = set()

    for item in items:
        src = item['source']
        if src.lower().startswith(('http://', 'https://')):
            unique_remote_urls.add(src)
            item['is_remote'] = True
        else:
            item['is_remote'] = False
            # Check local existence immediately
            if not os.path.exists(src):
                 print(f"  WARNING: Local file not found: {src}", file=sys.stderr)

    print(f"Found {len(items)} total items ({len(unique_remote_urls)} unique remote URLs).")

    # 3. Use a temporary directory for downloads
    with tempfile.TemporaryDirectory() as tmpdir:
        # Download unique remote files
        url_to_path_map = {}
        if unique_remote_urls:
            url_to_path_map = download_files(list(unique_remote_urls), tmpdir)

        # 4. Construct Final File List (Expanding Quantities)
        final_file_list_to_slice = []

        for item in items:
            path_to_add = None

            if item['is_remote']:
                if item['source'] in url_to_path_map:
                    path_to_add = url_to_path_map[item['source']]
            elif os.path.exists(item['source']):
                path_to_add = os.path.abspath(item['source'])

            # If we resolved a valid path, add it 'quantity' times
            if path_to_add:
                for _ in range(item['quantity']):
                    final_file_list_to_slice.append(path_to_add)

        # 5. Call PrusaSlicer
        try:
            run_prusa_slicer(final_file_list_to_slice, args.output_3mf_file)
        except Exception as e:
             print(f"A critical error occurred: {e}", file=sys.stderr)
             sys.exit(1)

    print("\nTemporary downloaded files have been cleaned up.")


if __name__ == '__main__':
    # Ensure 'requests' library is installed for downloading
    try:
        import requests
    except ImportError:
        print("The 'requests' library is required. Install it using: pip install requests", file=sys.stderr)
        sys.exit(1)

    # Ensure 'BeautifulSoup4' library is installed for scraping Thangs URLs
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Optional dependency, warned about in scraper function
        pass

    main()
