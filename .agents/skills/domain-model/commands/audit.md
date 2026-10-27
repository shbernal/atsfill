# audit: check the repo against the domain model

Compare `GLOSSARY.md` and the ADRs with code, docs and UI copy. Report, grouped by kind, with `file:line` references:

- Words listed under `_Avoid_` still in use.
- Recurring project-specific terms missing from the glossary.
- Glossary terms that no longer appear anywhere.
- Definitions the code contradicts.
- ADRs the code has drifted from.

Report only. Change the glossary or ADRs once the user agrees; suggest code renames, never apply them.
