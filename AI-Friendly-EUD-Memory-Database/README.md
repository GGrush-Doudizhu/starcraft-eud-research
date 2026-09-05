# AI-Friendly EUD Memory Database

Attach [euddb.tsv](euddb.tsv) to your AI chat and ask it to use the file as an EUD memory reference.

Thanks to FaRTy1billon for the original memory data and to Armoha for creating and maintaining [eud-book](https://github.com/armoha/eud-book). This file is generated from eud-book’s [offset_table.md](https://github.com/armoha/eud-book/blob/fb21c468f412ec41a61ec0b5b920516efae7eaaa/src/offset_table.md), which is based on FaRTy1billon’s data. Detailed descriptions and EPD values are not included.

## Regenerate

Run with Python 3 and your local eud-book table:

```powershell
python build_euddb.py --source "E:/eud-book/src/offset_table.md"
```

This overwrites `euddb.tsv`, including manual notes. Add `--output euddb.generated.tsv` to keep an edited copy.

## Token comparison

Measured with tiktoken 0.14.0 on the complete 654-entry exports. CSV, TSV, Markdown, and TXT share a single header; JSON repeats field names per entry. TXT used ` | ` between fields. These counts measure context use, not AI answer quality.

| Format | o200k_base | cl100k_base |
| --- | ---: | ---: |
| **TSV** | **12,350** | **12,451** |
| CSV | 12,662 | 12,763 |
| Markdown | 13,325 | 13,426 |
| TXT | 14,711 | 14,812 |
| JSON | 19,719 | 19,804 |
| TOML | 24,205 | 24,307 |
| XML | 28,547 | 28,629 |
| CBOR (Base64) | 40,100 | 43,027 |

Binary CBOR cannot be counted directly as text, so its Base64 representation was measured separately.
