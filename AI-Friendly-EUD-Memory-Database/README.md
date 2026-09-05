# AI-Friendly EUD Memory Database

**Attach [euddb.json](euddb.json) to your AI chat and ask the AI to use it as an EUD memory reference for your project.** It repackages the existing eud-book memory table into compact JSON, so the AI can read all the data from one file instead of browsing scattered pages.

## Format

A json file each entry retains `name`, `addr`, `size`, `len`, and `scr` when available. Addresses are hexadecimal strings such as `"0x58A364"`. Add an optional `note` manually when an entry needs explanation.

## Regenerate

Run with Python 3, supplying your local eud-book table:

```powershell
python build_euddb.py --source "E:/eud-book/src/offset_table.md"
```

This replaces `euddb.json` beside the script, including any manual notes. Use `--output euddb.generated.json` to keep an edited copy.

Special thanks to Armoha for creating and maintaining [eud-book](https://github.com/armoha/eud-book). This database extracts only the basic fields from its [memory table](https://github.com/armoha/eud-book/blob/fb21c468f412ec41a61ec0b5b920516efae7eaaa/src/offset_table.md); detailed entry pages are not included.
