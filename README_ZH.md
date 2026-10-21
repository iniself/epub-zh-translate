# EPUB 中文翻译 Skill

一个 [OpenCode](https://opencode.ai) skill：把英文 EPUB 翻译成简体中文，同时保留
原书结构、目录、锚点、代码块和行内标记。

这个 skill 调用本地零依赖流水线 `epubzh`：

- 抽取可翻译文本，冻结 `<pre>` 代码块；
- 把行内标记替换成占位符；
- 按 `large` / `small` 两种分块配置切分并翻译；
- 检查段数、占位符、未翻译内容和中英文空格；
- 审计术语一致性，并把已定译法带进后续分块；
- 每次从源 EPUB 重新构建，并校验 XML、链接和锚点。

## 分块配置

`large` 和 `small` 决定每个分块有多大，**必须在 `init` 之前选定**：

| 配置 | 每块 | 适用 |
| --- | --- | --- |
| `large`（默认） | 约 2600 词、不限段数 | 输出上限 ≥8k 的大模型 |
| `small` | 约 1200 词、最多 40 段 | 上下文或输出受限的模型，或之前翻译卡住过的书 |

在调用 skill 时写一行即可，不写就是 `large`：

```text
分块配置：small
```

## 环境要求

- Python 3.11 或更高版本（只用标准库）
- OpenCode
- macOS 或 Linux；Windows 建议使用 WSL

不需要安装 `pip`、`npm` 或其他第三方包。

## 安装

仓库根目录就是 skill 目录。OpenCode 根据目录名生成 skill ID
（`epub-zh-translate`），请保持这个目录名不变。

### 全局安装

```bash
git clone https://github.com/iniself/epub-zh-translate.git \
  ~/.config/opencode/skills/epub-zh-translate
```

### 项目内安装

```bash
git clone https://github.com/iniself/epub-zh-translate.git \
  <project>/.opencode/skills/epub-zh-translate
```

### 手动安装

下载仓库 ZIP 并解压，确保最终路径是：

```text
<skills-dir>/epub-zh-translate/SKILL.md
```

如果解压工具没有保留可执行权限，补上：

```bash
chmod +x ~/.config/opencode/skills/epub-zh-translate/epubzh-run
```

安装完成后重新打开一个 OpenCode 会话。

## 使用

在新会话中调用 skill，并提供书籍信息：

```text
@epub-zh-translate
源文件：/path/to/books/mybook.epub
中文书名：中文书名
原书名：Original English Title
作者：Author Name
输出：/path/to/books/mybook-zh.epub
项目目录（可选）：/path/to/books/mybook-zh-project
```

`源文件`、`中文书名`、`输出` 为必填；`原书名` 和 `作者` 建议填写。
`项目目录` 可选；不填时会按输出文件推导。agent 会先复述参数，得到确认后再开始。

你不需要手动运行任何命令行工具。agent 会在内部执行 `init`、`extract`、
`guide`、`check`、`build` 和 `verify`。

本文中的“项目根目录”指包含 `book.toml` 的书籍项目目录，不是 skill 目录。

如果没有指定项目目录，agent 会根据输出文件推导：

```text
<输出文件所在目录>/<输出文件名去掉 .epub>-project
```

例如输出是 `/path/to/books/mybook-zh.epub`，项目目录就是
`/path/to/books/mybook-zh-project`。最终 EPUB 仍在指定的输出路径；
`book.toml`、`src/` 和 `work/` 都在项目目录里。已有项目可以继续使用当前的
`book.toml`；这套推导规则适用于新项目。

## agent 会做什么

1. 创建项目配置、解包源 EPUB、生成术语表种子。
2. 阅读目录、前言和前两章，完善术语表。
3. 生成 `work/TRANSLATION_GUIDE.md`。
4. 按分块生成提示词，每个分块交给一个 subagent 翻译。
5. 逐块、逐章检查，审计术语，然后构建并校验最终 EPUB。
6. 汇报输出路径、分块配置、进度、`check`/`terms-audit`/`verify` 结果和遗留问题。

## 输出

- 你指定路径下的最终 EPUB。
- 一个工作项目，包含 `book.toml`、`work/TRANSLATION_GUIDE.md`、
  `work/terms.tsv`、`work/toc.tsv`、`work/pre.json` 和 `work/zh/`。

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

不要手工修改 `work/build`，每次构建都会从源 EPUB 重新生成。

## 适用书籍

适合以文字为主的 EPUB 2 / EPUB 3 技术书和非虚构类书籍。

不适合固定版式书籍、漫画、扫描书或以图片为主的书。

## 常见问题

- **skill 没有出现**：确认
  `.../skills/epub-zh-translate/SKILL.md` 存在，目录名没有被改，然后重启
  OpenCode。
- **找不到工具**：检查 Python 3.11+、`epubzh-run` 的可执行权限，以及 skill
  目录是否被移动。
- **`check` 报错**：修改 `work/zh/` 中对应的分块；不要修改 `work/build`。
  子代理自检请用 `check <章名> --chunk <n>`，整章检查只会报“其他分块缺失”。
- **模型反复说“我将翻译”但不写文件**：通常是分块太大或没有用生成的提示词。改用
  `small` 配置，确认执行的是 `work/prompts/<stem>.<nn>.md`，一个分块一个 subagent；
  子代理若只回复计划，重发一句“读提示词文件，直接写文件，不要回复计划”。
- **术语前后不一致**：运行 `terms-audit` 看命中率；把合理变体补进 `terms.tsv` 第三列。
  术语表第二列只放规范译名，说明文字放第三列。
- **想中途换 `large`/`small`**：不行。切换会重新切分块，已有译文对不上；只能在
  `extract` 之前决定。
- **需要严格 EPUB 校验**：`verify` 不能替代 `epubcheck`，如有需要请单独安装
  `epubcheck`。

仅供排查时手动运行：

```bash
TOOL=~/.config/opencode/skills/epub-zh-translate/epubzh-run
"$TOOL" status --config /path/to/project/book.toml
"$TOOL" <command> --help
```

## 仓库结构

```text
epub-zh-translate/
├── SKILL.md              # 给 agent 的流程和工具调用契约
├── README.md             # 英文安装与使用说明
├── README_ZH.md          # 中文安装与使用说明
├── LICENSE               # MIT
├── .gitignore
├── book.toml.example     # 项目配置模板
├── epubzh-run            # 启动器
└── epubzh/               # Python 包（只用标准库）
    ├── ...
    └── defaults/
        ├── guide-template.md
        ├── terms.tsv
        └── zh-overrides.css
```

## 许可证

MIT，详见 [LICENSE](LICENSE)。
