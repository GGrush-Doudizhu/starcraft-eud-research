# StarCraft EUD Research

Research notes on StarCraft EUD mechanics, implementation, tooling, and optimization.

This repository studies EUD itself rather than any single library or control-flow feature. Most research is expected to focus on eudplib because it is currently the mainstream tool used to generate EUD logic, but the scope is not limited to eudplib.

Each research project has its own top-level directory and may contain documents, source code, or a complete project.

## Projects

| Project | Resources |
| --- | --- |
| `AI-Friendly-EUD-Memory-Database` | [AI-ready JSON reference](AI-Friendly-EUD-Memory-Database/euddb.json) · [Generator](AI-Friendly-EUD-Memory-Database/build_euddb.py) · [English documentation](AI-Friendly-EUD-Memory-Database/README.md) |
| `EUDIf-Control-Flow-Mechanics-and-Optimization-Practices` | [English article](EUDIf-Control-Flow-Mechanics-and-Optimization-Practices/EUDIf-Control-Flow-Mechanics-and-Optimization-Practices.md) · [Simplified Chinese article](EUDIf-Control-Flow-Mechanics-and-Optimization-Practices/EUDIf%E6%8E%A7%E5%88%B6%E6%B5%81%E6%9C%BA%E5%88%B6%E4%B8%8E%E4%BC%98%E5%8C%96%E5%AE%9E%E8%B7%B5.md) |
| `EUD-Variable-Pool-Design-and-Implementation` | [Source code](EUD-Variable-Pool-Design-and-Implementation/eud_vars.py) · [English documentation](EUD-Variable-Pool-Design-and-Implementation/eud_vars.md) · [Simplified Chinese documentation](EUD-Variable-Pool-Design-and-Implementation/eud_vars_zh-CN.md) |

## Related Tool and Acknowledgments

[eudplib](https://github.com/armoha/eudplib) is currently the primary tool used in most of this repository's research.

Special thanks to **Armoha** for his long-term dedication to maintaining eudplib and for his help. Thanks also to **trgk**, the original author of eudplib.

## Author

**GGrush**
