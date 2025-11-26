#!/usr/bin/env python3
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
import sys
import os
import urllib.request
import urllib.parse
import json
import time
import re

def local_name(tag):
    """
    Returns the local name of an XML tag, stripping the namespace.
    Example: '{http://schemas.microsoft.com/3mf/2013/3/3mf}model' -> 'model'
    """
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag

def clean_part_name(name):
    """
    Removes .stl extension from the name if present (case-insensitive).
    """
    if name and name.lower().endswith('.stl'):
        return name[:-4]
    return name

def find_child_by_name(element, name):
    """Finds the first direct child with a specific local name (ignoring namespace)."""
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None

def find_all_children_by_name(element, name):
    """Finds all direct children with a specific local name (ignoring namespace)."""
    found = []
    for child in element:
        if local_name(child.tag) == name:
            found.append(child)
    return found

def get_metadata_value(element, key_name):
    """
    Helper to find <metadata key="...">value</metadata>
    or <metadata key="..." value="..."/>
    """
    for meta in element.iter():
        if local_name(meta.tag) == 'metadata':
            if meta.get('key') == key_name:
                # Check 'value' attribute first (common in model_settings.config)
                val = meta.get('value')
                if val:
                    return val
                # Fallback to text content
                if meta.text:
                    return meta.text.strip()
    return None

def extract_names_from_config(zf):
    """
    Parses Metadata/model_settings.config (or .xml) to create a map of Object ID -> List of Part Names.
    """
    possible_paths = ['Metadata/model_settings.config', 'Metadata/model_settings.xml']
    config_path = None
    for p in possible_paths:
        if p in zf.namelist():
            config_path = p
            break

    if not config_path:
        return {}

    id_to_names = {} # Maps object_id (str) -> list of names [str]

    try:
        with zf.open(config_path) as f:
            tree = ET.parse(f)
            root = tree.getroot()
            config = find_child_by_name(root, 'config')

            # If config element isn't found (e.g. root IS config), fallback to root
            search_root = config if config is not None else root

            # Find all 'object' elements anywhere in the tree
            for obj in search_root.iter():
                if local_name(obj.tag) == 'object':
                    obj_id = obj.get('id')
                    if not obj_id:
                        continue

                    # Find all 'part' children of this object
                    parts = []
                    for child in obj:
                        if local_name(child.tag) == 'part':
                            parts.append(child)

                    names_for_this_object = []

                    # Logic: If 1 or 0 parts, use Object Metadata. If >1 parts, use Part Metadata.
                    if len(parts) <= 1:
                        # Use the Object's metadata name
                        name = get_metadata_value(obj, 'name')
                        if name:
                            names_for_this_object.append(clean_part_name(name))
                    else:
                        # Use each Part's metadata name
                        for part in parts:
                            p_name = get_metadata_value(part, 'name')
                            if p_name:
                                names_for_this_object.append(clean_part_name(p_name))
                            else:
                                names_for_this_object.append(f"Unnamed Component of Object {obj_id}")

                    # Only add to map if we found names
                    if names_for_this_object:
                        id_to_names[str(obj_id)] = names_for_this_object

    except Exception as e:
        print(f"Warning: Found {config_path} but failed to parse it: {e}")

    return id_to_names

def search_thangs(query):
    """
    Generates a direct search link for Thangs.com using the frontend URL format.
    Example: https://thangs.com/search/"query"?searchScope=thangs&view=list
    Skips generation if query matches specific patterns.
    """
    if not query:
        return []

    # Skip URL generation for stacking tiles (likely utility parts)
    if re.search(r"Tile.* Stack", query):
        return []

    # 1. Enclose the query in double quotes as requested
    quoted_query = f'"{query}"'

    # 2. URL Encode the query (spaces -> %20, quotes -> %22)
    # quote() uses %20 for spaces, which matches the user's requirement better than quote_plus()
    encoded_query = urllib.parse.quote(quoted_query)

    # 3. Construct the URL
    url = f"https://thangs.com/search/{encoded_query}?searchScope=thangs&view=list"

    # Return as a list to maintain compatibility with the BOM printing loop
    return [url]

def parse_3mf_for_bom(filepath: str):
    """
    Reads a 3MF file, parses the internal 3D model XML, and prints a Bill of Materials (BOM).
    """
    if not os.path.exists(filepath):
        print(f"Error: File not found at '{filepath}'")
        return

    print(f"--- Analyzing 3MF File: {os.path.basename(filepath)} ---")

    try:
        with zipfile.ZipFile(filepath, 'r') as zf:
            model_xml_content = None

            # Check for the two common paths as 3MF is case-sensitive
            try:
                model_xml_content = zf.read('3D/3dmodel.model')
            except KeyError:
                try:
                    model_xml_content = zf.read('3d/3dmodel.model')
                except KeyError:
                    raise KeyError("The 3MF archive is missing the required '3D/3dmodel.model' or '3d/3dmodel.model' file.")

            # Pre-load metadata names. Returns Dict[ID -> List[Names]]
            metadata_names_map = extract_names_from_config(zf)

    except Exception as e:
        print(f"Error accessing 3MF file: {e}")
        return

    # Parse the main model XML
    try:
        root = ET.fromstring(model_xml_content)
    except ET.ParseError as e:
        print(f"Error: Failed to parse XML in 3dmodel.model: {e}")
        return

    # Extract generic names from <resources> as fallback
    # These are usually single strings
    resource_names_fallback = {}
    resources = find_child_by_name(root, 'resources')

    if resources is not None:
        for obj in find_all_children_by_name(resources, 'object'):
            object_id = obj.get('id')
            object_name = obj.get('name')
            if object_id:
                resource_names_fallback[object_id] = clean_part_name(object_name)

    # Extract build items
    build = find_child_by_name(root, 'build')

    final_bom_list = []

    if build is not None:
        for item in find_all_children_by_name(build, 'item'):
            object_id = item.get('objectid')
            if object_id:
                # 1. Try Metadata Config (Specific Slicer Settings)
                # This might return a LIST of names if the object has multiple parts
                if object_id in metadata_names_map:
                    final_bom_list.extend(metadata_names_map[object_id])

                # 2. Try Standard 3MF Resources
                elif object_id in resource_names_fallback and resource_names_fallback[object_id]:
                    final_bom_list.append(resource_names_fallback[object_id])

                # 3. Fallback
                else:
                    final_bom_list.append(f"Unnamed Object (ID: {object_id})")
    else:
        if resources is not None:
             print("Warning: Found resources but could not find '<build>' section.")

    # Compile BOM
    bom = Counter(final_bom_list)

    print("\nBill of Materials (BOM):")

    if not bom:
        print("No parts were found in the model's build section.")
        return

    print("-" * 35)
    print(f"{'Quantity':<10} | {'Part Name':<40} | {'Thangs URL'}")
    print("-" * 100)

    # Sort by Name (Case-insensitive)
    sorted_bom_items = sorted(bom.items(), key=lambda x: x[0].lower())

    for name, count in sorted_bom_items:
        # Generate Thangs search URL
        thangs_urls = search_thangs(name)

        # Format output
        output_line = f"{count:<10} | {name:<40}"
        if thangs_urls:
            urls_str = ", ".join(thangs_urls)
            output_line += f" | {urls_str}"

        print(output_line)

    print("-" * 100)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 3mf_bom_parser.py <path_to_3mf_file>")
        sys.exit(1)

    input_file = sys.argv[1]
    parse_3mf_for_bom(input_file)
