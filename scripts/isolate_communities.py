#!/usr/bin/env python3
"""Stage 2.6: ISOLATE Mode Script

Prunes all semantic links (Related Nodes) that cross community boundaries.
After this script, only links within the same community (e.g., BATCH_GELD) remain.
Cross-community edges and links to non-community nodes are deleted.

Usage:
    python3 isolate_communities.py
"""

import os
import re
import sys

# Adjust imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault_parser import VAULT

COMMUNITY_RE = re.compile(r"\*\*Community:\*\*\s*(.*)", re.IGNORECASE)
RELATED_RE = re.compile(r"(- \*\*Related Nodes:\*\*\s*)(.*)", re.IGNORECASE)

def get_orphan_metadata(vault_path):
    """Maps each orphan word to its community and stores its content/path."""
    meta = {}
    status_pattern = re.compile(r"Integration Status:.*?\s*Matched / Unpartitioned", re.IGNORECASE)
    
    for fn in os.listdir(vault_path):
        if not fn.endswith(".md"): continue
        path = os.path.join(vault_path, fn)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            if status_pattern.search(content):
                word = fn[:-3]
                comm_match = COMMUNITY_RE.search(content)
                community = comm_match.group(1).strip() if comm_match else "Isolated"
                meta[word] = {"community": community, "path": path, "content": content}
    return meta

def main():
    print("Gathering orphan metadata...")
    meta = get_orphan_metadata(VAULT)
    if not meta:
        print("No matched orphaned words found to isolate.")
        return

    print(f"Isolating {len(meta)} nodes into their respective communities...")
    
    count = 0
    pruned_links_total = 0
    
    for word, data in meta.items():
        current_comm = data["community"]
        content = data["content"]
        
        # Find existing links
        rel_match = RELATED_RE.search(content)
        if not rel_match:
            continue
            
        links_str = rel_match.group(2).strip()
        if not links_str or links_str == "None":
            continue
            
        # Extract individual wikilinks
        links = re.findall(r"\[\[(.*?)\]\]", links_str)
        valid_links = []
        pruned_here = 0
        
        for target in links:
            # Rule: Keep link only if target is an orphan in the SAME community
            # AND the community is not Isolated
            if target in meta and meta[target]["community"] == current_comm and current_comm.lower() != "isolated":
                valid_links.append(f"[[{target}]]")
            else:
                pruned_here += 1
        
        # Update content if links were pruned
        if pruned_here > 0:
            new_links_str = ", ".join(valid_links) if valid_links else "None"
            new_content = RELATED_RE.sub(rf"\g<1>{new_links_str}", content)
            
            # If everything was pruned, maybe change Role to Isolated
            if not valid_links:
                new_content = re.sub(r"(\*\*Role:\*\*\s*)Normal Node", r"\g<1>Isolated", new_content)

            with open(data["path"], "w", encoding="utf-8") as f:
                f.write(new_content)
            
            count += 1
            pruned_links_total += pruned_here

    print(f"\nISOLATE Phase Complete.")
    print(f"- Files updated: {count}")
    print(f"- Cross-community links pruned: {pruned_links_total}")

if __name__ == "__main__":
    main()
