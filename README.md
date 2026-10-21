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

### Manual installation

Download the repository as a ZIP, extract it, and place the folder so that this
file exists:

```text
<skills-dir>/epub-zh-translate/SKILL.md
```

Make the launcher executable if your archive tool did not preserve permissions:

```bash
chmod +x ~/.config/opencode/skills/epub-zh-translate/epubzh-run
```

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
  sees its own chunk.
- **The model keeps saying "I'll translate it" without writing**: the chunk is
  probably too large, or the generated prompt was not used. Switch to `small`,
  run `prompt` and hand `work/prompts/<stem>.<nn>.md` to one subagent per chunk.
- **Terminology drifts**: run `terms-audit`; put acceptable variants in the third
  column of `terms.tsv`. Keep the second column to a single canonical rendering.
- **Changing the chunk profile mid-project**: not supported. It renumbers chunks
  and invalidates existing translations.
- **Strict EPUB validation**: `verify` is not a replacement for `epubcheck`.
  Install `epubcheck` separately if you need full conformance checks.

For manual diagnosis only:

```bash
TOOL=~/.config/opencode/skills/epub-zh-translate/epubzh-run
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
