# Changelog

## [2.5.1] - 2025-06-14

### ✨ Added
- Support for more flexible ZIP layouts: compilation now works even if all files are inside a single top-level folder (e.g., exports from Overleaf or GitHub).
- Integration with `latexmk` for automatic handling of multiple compilation passes and bibliography tools like BibTeX.

### 🔄 Changed
- Replaced manual LaTeX compilation logic with `latexmk`, simplifying the flow and improving reliability.
- `options` payload now supports only one field: `main_file` (defaults to `main.tex`).
- Removed previously supported options: `num_runs` and `use_bibtex`, as `latexmk` manages this automatically.

### 🧹 Cleaned
- Refactored internal code for maintainability and easier debugging.
- Updated README to reflect simplified API and new behavior.

### 🐛 Fixed
- Resolved race condition when handling multiple incoming ZIP file uploads with the same structure.

---

## [2.4.1] - 2025-06-14
- Support for nested folder in zip file

