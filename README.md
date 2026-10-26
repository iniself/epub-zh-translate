# EPUB Chinese Translation Skill

An [OpenCode](https://opencode.ai) skill that translates English EPUB books into
Simplified Chinese while preserving the book's structure, table of contents,
anchors, code blocks, and inline markup.

The skill drives a local, dependency-free pipeline (`epubzh`) that:

- extracts translatable text and freezes `<pre>` blocks;
- replaces inline markup with placeholders;
- translates in parallel chunks;
- checks segment counts, placeholders, untranslated text, and CJK/Latin spacing;
- rebuilds the EPUB from the original source and verifies XML, links, and anchors.

## Requirements

- Python 3.11 or newer (standard library only)
- OpenCode
- macOS or Linux. On Windows, use WSL.

No `pip`, `npm`, or other packages are required.

## Installation

The repository root is the skill directory. OpenCode derives the skill ID from
the directory name (`epub-zh-translate`), so keep that name.

### Global installation

```bash
git clone https://github.com/iniself/epub-zh-translate.git \
  ~/.config/opencode/skills/epub-zh-translate
```

### Project-local installation

```bash
git clone https://github.com/iniself/epub-zh-translate.git \
  <project>/.opencode/skills/epub-zh-translate
```

A project-local install behaves exactly like a global one. The book project
(`book.toml`, `work/`, and so on) is still created at the location you pass via
`--base` or as an absolute path; nothing is written into the skill directory.

- Keep the directory name `epub-zh-translate`; renaming it breaks
  `@epub-zh-translate`.
- If the same skill exists globally and in the project, the project copy wins.
- OpenCode finds it when started from the project root or any subdirectory.

### Manual installation

Download the repository as a ZIP, extract it, and place the folder so that this
file exists:

```text
<skills-dir>/epub-zh-translate/SKILL.md
```

Make the launcher executable if your archive tool did not preserve permissions:

```bash
chmod +x <skills-dir>/epub-zh-translate/epubzh-run
```

`<skills-dir>` is the actual install location: `~/.config/opencode/skills` for a
global install, or `<project>/.opencode/skills` for a project-local install.

Start a new OpenCode session after installation.

## Usage

In a new session, invoke the skill with the book information:

```text
@epub-zh-translate
Source: /path/to/books/mybook.epub
Chinese title: 中文书名
Original title: Original English Title
Author: Author Name
Output: /path/to/books/mybook-zh.epub
Project directory (optional): /path/to/books/mybook-zh-project
Chunk profile (optional): small
```

`Source`, `Chinese title`, and `Output` are required. `Original title` and
`Author` are recommended. `Project directory` is optional; if omitted, it is
derived from the output path. `Chunk profile` is optional and defaults to
`large`; see below. The agent will repeat the parameters and wait for
confirmation before starting.

### Chunk profiles

`large` and `small` decide how much text one chunk holds. Choose before
`extract`; switching later renumbers chunks and invalidates translations.

| Profile | Per chunk | Use it for |
| --- | --- | --- |
| `large` (default) | about 2600 words, no segment cap | models with an 8k+ output window |
| `small` | about 1200 words, at most 40 segments | smaller context/output windows, or books that stalled before |

Write `Chunk profile: small` (or `分块配置：small`) when invoking the skill.

### Paths

**Best practice: put both the source EPUB and the output EPUB in the directory
where you started opencode (the session directory), and give only file names.**

```text
@epub-zh-translate

Source: mybook.epub
Chinese title: 中文书名
Original title: Original Title
Author: Author
Output: mybook-zh.epub
Chunk profile: small
```

The project directory becomes `mybook-zh-project/` next to the output file.

Relative paths are resolved against the **session directory**, never against
the directory a command happens to run in:

| Source | Output | Result |
| --- | --- | --- |
| file name | file name | both in the session directory |
| absolute | file name | output and project live in the session directory |
| file name | absolute | source is looked up in the session directory; project follows the output |
| absolute | absolute | both used as given |

Additional rules:

- If a bare file name is not in the session directory, the tool also checks the
  output directory (the parent of the project directory).
- If it still cannot find the file, it lists the directories it searched and the
  EPUBs it found; it never guesses another directory.
- `book.toml` stores relative paths, so your home directory is not written into
  the project.
- There is no need to `cd` into the project directory before running commands.

You do not need to run any command-line tool. The agent runs `init`, `extract`,
`guide`, `check`, `build`, and `verify` internally.

In this guide, "project root" means the book project directory containing
`book.toml`, not the skill directory.

If you do not specify a project directory, the agent derives it from the output
path:

```text
<output directory>/<output name without .epub>-project
```

For example, an output of `/path/to/books/mybook-zh.epub` creates the project
at `/path/to/books/mybook-zh-project`. The final EPUB stays at the output path;
`book.toml`, `src/`, and `work/` live inside that project directory. Existing
projects can keep their current `book.toml`; the derivation applies when
starting a new project.

## What the agent does

1. Creates the project configuration, unpacks the source EPUB, and seeds a glossary.
2. Reads the table of contents, preface, and first chapters to refine the glossary.
3. Generates `work/TRANSLATION_GUIDE.md`.
4. Generates one prompt per chunk under `work/prompts/` and hands each chunk to a
   subagent, one chunk per subagent.
5. Checks each chunk and chapter, audits terminology, then builds and verifies the
   final EPUB.
6. Reports the output path, chunk profile, progress, check/audit/verify results, and
   any remaining issues.

## Output

- The final EPUB at the path you specify.
- A working project containing `book.toml`, `work/TRANSLATION_GUIDE.md`,
  `work/terms.tsv`, `work/toc.tsv`, `work/pre.json`, and `work/zh/`.

```text
/path/to/books/
├── mybook-zh.epub
└── mybook-zh-project/
    ├── book.toml
    ├── src/
    └── work/
        ├── TRANSLATION_GUIDE.md
        ├── terms.tsv
        ├── toc.tsv
        ├── pre.json
        └── zh/
```

Do not edit `work/build` by hand. It is regenerated from the source EPUB on every
build.

## Supported books

Suitable for text-heavy EPUB 2 and EPUB 3 technical books and non-fiction.

Not suitable for fixed-layout books, comics, or scanned/image-based books.

## Troubleshooting

- **The skill does not appear**: confirm that
  `.../skills/epub-zh-translate/SKILL.md` exists, keep the directory name, and
  restart OpenCode.
- **The tool cannot be found**: check that Python 3.11+ is available, that
  `epubzh-run` is executable, and that the skill directory was not moved.
- **`check` reports problems**: fix the reported chunk under `work/zh/`; do not
  edit `work/build`. For a subagent, use `check <stem> --chunk <n>` so it only
  sees its own chunk. `check` repairs duplicate blocks, drops out-of-range
  blocks, sorts out-of-order chunks, and removes an extra closing marker for a
  single `⟦n⟧`; ambiguous duplicates keep the last copy with a warning. Use
  `--no-fix` for a read-only check. A project-wide `check` skips chunks that
  have not been started; use `check --all` for final validation.
- **Resume an existing project**: run `next --config ...`; by default it follows
  reading order and selects the first unfinished chunk. Run `next` again after
  that chunk passes. Add `--resume` only when the user wants to continue an
  interrupted or broken chunk first. Do not start with a project-wide `check`.
- **The model keeps saying "I'll translate it" without writing**: the chunk is
  probably too large, or the generated prompt was not used. Switch to `small`,
  run `prompt` and hand `work/prompts/<stem>.<nn>.md` to one subagent per chunk.
- **Terminology drifts**: run `terms-audit`; put acceptable variants in the third
  column of `terms.tsv`. Keep the second column to a single canonical rendering.
- **Changing the chunk profile mid-project**: not supported. It renumbers chunks
  and invalidates existing translations.
- **`init` reports `source not found`**: put the source EPUB next to the output
  file, or pass the correct relative/absolute path in the prompt. The error lists
  the directories it searched and the EPUBs it found; fix the path instead of
  trying other directories.
- **Strict EPUB validation**: `verify` is not a replacement for `epubcheck`.
  Install `epubcheck` separately if you need full conformance checks.

For manual diagnosis only:

```bash
TOOL=<skills-dir>/epub-zh-translate/epubzh-run
"$TOOL" next --config /path/to/project/book.toml
"$TOOL" next --resume --config /path/to/project/book.toml
"$TOOL" status --config /path/to/project/book.toml
"$TOOL" <command> --help
```

## Repository layout

```text
epub-zh-translate/
├── SKILL.md              # Agent instructions and tool contract
├── README.md             # English installation and usage guide
├── README_ZH.md          # Chinese installation and usage guide
├── LICENSE               # MIT
├── .gitignore
├── book.toml.example     # Project configuration template
├── epubzh-run            # Launcher
└── epubzh/               # Python package (standard library only)
    ├── ...
    └── defaults/
        ├── guide-template.md
        ├── terms.tsv
        └── zh-overrides.css
```

## License

MIT. See [LICENSE](LICENSE).
