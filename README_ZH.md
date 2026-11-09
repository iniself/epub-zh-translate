# EPUB 中文翻译 Skill

一个 [OpenCode](https://opencode.ai) skill：把英文 EPUB 翻译成简体中文，同时保留
原书结构、目录、锚点、代码块和行内标记。

这个 skill 调用本地零依赖流水线 `epubzh`：

- 抽取可翻译文本，冻结 `<pre>` 代码块；
- 把行内标记替换成占位符；
- 按 `large` / `small` / `mini` / `tiny` 四种分块配置切分并翻译；
- 检查段数、占位符和未翻译内容，包括抄进译文的源文短语、用 `@@@VERBATIM@@@`
  跳过散文的情况（中英文空格由 `--fix` 自动补齐）；
- 修不好的分块登记到人工介入清单，翻译继续，合并前再拦截；
- 审计术语一致性，并把已定译法带进后续分块；`terms.tsv` 同时支持“译成固定中文”
  和“保留英文”两种决定；
- 出书前做一次统一体检：术语、人工介入、试合并、XML/链接/锚点问题一次全部列出，
  全部干净才生成成品；
- 每次从源 EPUB 重新构建，并校验 XML、链接和锚点。

## 分块配置

四档配置决定每个分块有多大，**必须在 `init` 之前选定**：

| 配置 | 每块 | 适用 |
| --- | --- | --- |
| `large`（默认） | 约 2600 词、不限段数 | 输出上限 ≥8k 的大模型 |
| `small` | 约 1200 词、最多 40 段 | 上下文或输出受限的模型，或之前翻译卡住过的书 |
| `mini` | 约 1000 词、最多 25 段 | 弱模型首选；表格、列表多的书比 `small` 更稳 |
| `tiny` | 约 600 词、最多 20 段 | 会陷入思考循环、或输出窗口很小的模型 |

`large` 之外的 `small` / `mini` / `tiny` 都是非默认档，区别只在尺寸依次递减、
适用模型从强到弱；四档都只能在 `extract` 之前选定，已有译文的项目换不了档。

词数和段数是两个上限，谁先到谁生效：散文通常词数先到，表格和列表通常段数先到。

在调用 skill 时写一行即可，不写就是 `large`：

```text
分块配置：<档位>
```

## 翻译格式（进阶）

`book.toml` 的 `[translate] mode` 决定行内标记怎么还原。新项目默认 `inline`；
要 `aligned` 时，在调用里写“模式：aligned”（或 `mode: aligned`），agent 会把它
映射成 `init --mode aligned`。一本书从头到尾只用一种模式。

- `inline`（默认）：模型在译文里直接写 `⟦n⟧` / `⟦/n⟧` 标记，输出更短，适合
  输出预算有限的小模型。
- `aligned`：模型每段只写一个流畅的 `@@@TEXT@@@`，再按原文成对标记写
  `@@@SPAN k@@@`。程序检查每个 SPAN 是否逐字出现在 TEXT 中，并按原文校验
  顺序和嵌套，然后自动插回标记。找不到、歧义或嵌套冲突都会报精确错误，
  不会静默生成坏 HTML；输出比 inline 长。

这些标记由模型写进 `work/zh/`，你不需要自己输入。模式在**开始翻译第一块之前**
确定；开翻后再切换，已有的分块会因格式不符被判错，只能逐块重译。也可以直接改
`book.toml` 里的 `mode`，但同样要在开翻前。这个设置只影响翻译，不需要重跑
`extract`。

## 可选：限制子代理步数

如果客户端支持 subagent 配置，可以给翻译子代理加 `steps`，防止模型卡住后无限调用：

```jsonc
"agents": {
  "translate": {
    "mode": "subagent",
    "description": "Translates one EPUB chunk",
    "steps": 28
  }
}
```

`steps` 是 agent 迭代上限，建议 25–30；6 轮正常修复大约需要 20 次调用，设得太小会误杀。
**不配置也能用**：skill 优先派 `translate`，不存在时自动退回 `general`，只是没有硬上限。
修改后需要重启客户端。

## 路径怎么写

**最佳实践：把源 EPUB 和输出 EPUB 都放在启动 opencode 的目录（会话目录），
prompt 里只写文件名。**

```text
@epub-zh-translate

源文件：mybook.epub
中文书名：中文书名
原书名：Original Title
作者：Author
输出：mybook-zh.epub
分块配置：small
```

项目目录会自动落在会话目录下的 `mybook-zh-project/`。

所有相对路径都以**会话目录**为基准，与命令在哪执行无关：

| 源文件 | 输出 | 结果 |
| --- | --- | --- |
| 文件名 | 文件名 | 都在会话目录 |
| 绝对路径 | 文件名 | 输出和项目目录在会话目录 |
| 文件名 | 绝对路径 | 源在会话目录找，项目目录跟着输出走 |
| 绝对路径 | 绝对路径 | 都按原路径 |

补充规则：

- 源文件只给文件名、又不在会话目录时，工具会再去输出目录（项目目录的上一级）找。
- 仍找不到会报错，列出搜过的目录和候选 epub，不会自己换目录猜。
- `book.toml` 保存相对路径，不会把你的家目录写进项目文件。
- 不需要 `cd` 到项目目录再执行命令；agent 也不应该这样做。

## 环境要求

- Python 3.11 或更高版本（只用标准库）
- OpenCode
- macOS、Linux，或 Windows 10/11（WSL 或原生 PowerShell 均可）

不需要安装 `pip`、`npm` 或其他第三方包。

## 源语言支持

目标语言固定为简体中文。源书目前必须是使用拉丁字母、以空格分词的语言，
例如英语、法语、德语、西班牙语、意大利语、葡萄牙语。

暂不支持无空格语言（日语、韩语、泰语）和非拉丁字母语言（俄语、希腊语、
阿拉伯语、希伯来语）：流水线按拉丁字母判断可翻译段落、按空格计算分块大小，
这些书会漏段或分块异常。

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

项目内安装和全局安装的功能完全一致。翻译项目（`book.toml`、`work/` 等）仍然
按 `--base` 或绝对路径落在你指定的位置，不会写进 skill 目录。

- 目录名必须保持 `epub-zh-translate`；改名后 `@epub-zh-translate` 会失效。
- 全局和项目里装了同一个 skill 时，项目内那份优先。
- 在项目根目录或它的子目录启动 OpenCode 都能发现该 skill。

### 用 skills CLI 安装（可选）

也可以用第三方 `skills` CLI 安装，效果和 `git clone` 相同：

```bash
# 项目内（默认）
npx skills add iniself/epub-zh-translate -a opencode

# 全局
npx skills add iniself/epub-zh-translate -a opencode -g
```

- 不加 `-g` 时安装到当前项目的 `.opencode/skills/`；`-g` 安装到
  `~/.config/opencode/skills/`。
- 安装目录名取自 `SKILL.md` 的 `name`（`epub-zh-translate`）。
- 交互式安装会询问 Symlink 还是 Copy：Symlink 会在 `.agents/skills/` 保留一份
  并软链到 OpenCode；只想保留一份拷贝时选 Copy。
- 安装后同样用 `@epub-zh-translate` 调用。
- 这种方式需要 Node.js，但只在安装时用到；运行 skill 仍然只需要 Python。

### Windows 原生安装

Windows 不需要 WSL。用上面的 `git clone` 或 `npx skills add` 安装即可，
不需要 `chmod`。`epubzh-run.cmd` 会自己处理 `PYTHONPATH`：优先用 `python`，
没有时回退到 `py -3`，并检查版本是否 3.11 以上。

### 手动安装

下载仓库 ZIP 并解压，确保最终路径是：

```text
<skills-dir>/epub-zh-translate/SKILL.md
```

如果解压工具没有保留可执行权限，补上：

```bash
chmod +x <skills-dir>/epub-zh-translate/epubzh-run
```

`<skills-dir>` 是实际安装位置：全局为 `~/.config/opencode/skills`，项目内为
`<project>/.opencode/skills`。Windows 不需要这一步，直接使用 `epubzh-run.cmd`。

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

输出文件名可以包含空格、括号和中文，agent 会自动加引号；为了更稳，建议只用中文、
字母、数字、`-`、`_`。

你不需要手动运行任何命令行工具。agent 会在内部执行 `init`、`extract`、
`guide`、`check`、`build` 和 `verify`。Windows 下调用方式完全相同，agent 会
自动使用 `epubzh-run.cmd`，不需要 WSL。

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

1. 创建项目配置、解包源 EPUB、生成空的术语表（只有表头）。
2. 阅读目录、前言和前两章（实际没有就跳过，不手工创建目录），完善术语表；之后
   每个分块通过后继续追加新术语和专名。
3. 生成 `work/TRANSLATION_GUIDE.md`。
4. 按分块生成提示词，每个分块交给一个 subagent 翻译。
5. 逐块、逐章检查，然后构建并校验最终 EPUB；`build` 会先做统一体检，把术语、人工
   介入、试合并、结构问题一次全部列出，全干净才出成品。
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
- **找不到工具**：检查 Python 3.11+，确认 `epubzh-run`（Windows 为
  `epubzh-run.cmd`）；macOS / Linux 还要检查 `epubzh-run` 的可执行权限，
  以及 skill 目录是否被移动。
- **`check` 报错**：修改 `work/zh/` 中对应的分块；不要修改 `work/build`。
  子代理自检请用 `check <章名> --chunk <n>`，整章检查只会报“其他分块缺失”。
  `check` 默认会自动修复重复段、越界段、乱序，以及单独占位符 `⟦n⟧` 后面多出来的
  `⟦/n⟧`；歧义重复段保留最后一份并警告。只读检查用 `--no-fix`。
  项目级 `check` 默认跳过尚未开始的分块，最终验收用 `check --all`。
- **继续已有项目**：运行 `next --config ...`，默认按阅读顺序给出第一个未完成分块
  和对应的 prompt 文件；处理完再运行 `next`。只有要恢复上次中断或损坏的分块时，
  才用 `next --config ... --resume` 让 repair/partial/broken 优先。不要先跑全项目
  `check`。
- **模型反复说“我将翻译”但不写文件，或在思考里自我循环**：先用
  `prompt <章名> --chunk <n> --brief` 生成极简提示词，只发一句“读这个文件，第一步是
  write”；仍不动手就让主会话直接翻译这一块，或换模型、降低 thinking 深度。
  **不要第 3 次把同一份提示词派给同一个模型。** 弱模型的新项目可以从 `small`、
  `mini`、`tiny` 里选更小的档，档位越小，每块要求模型写的内容越少。
- **术语前后不一致**：运行 `terms-audit` 看命中率；同一术语的可接受译法写进 `terms.tsv`
  第二列，用 `、` 或 `；` 分隔。中文列重复英文、或写 `keep` / `-`，表示该词保留英文
  （只用于专名和标题，不能拿整句来保留）。`build` 会按术语表硬性拦截，不一致时先改
  译文或术语表，不要绕过。
- **想中途换分块配置**：不行。四档都只能在 `extract` 之前决定；切换会重新切分块，
  已有译文对不上。
- **`init` 报 source not found**：把源 EPUB 放到输出目录（项目目录的上一级），或在
  prompt 里给出正确的相对/绝对路径。报错会列出搜过的目录和候选文件，照着改，
  不要反复换目录试。
- **需要严格 EPUB 校验**：`verify` 不能替代 `epubcheck`，如有需要请单独安装
  `epubcheck`。

仅供排查时手动运行：

```bash
# macOS / Linux
TOOL=<skills-dir>/epub-zh-translate/epubzh-run

# Windows PowerShell
# $TOOL = "<skills-dir>\epub-zh-translate\epubzh-run.cmd"
# 调用时用 & $TOOL，例如：& $TOOL next --config ...
"$TOOL" next --config /path/to/project/book.toml
"$TOOL" next --resume --config /path/to/project/book.toml
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
├── epubzh-run            # macOS / Linux 启动器
├── epubzh-run.cmd        # Windows 启动器
└── epubzh/               # Python 包（只用标准库）
    ├── ...
    └── defaults/
        ├── guide-template.md
        ├── terms.tsv
        └── zh-overrides.css
```

## 许可证

MIT，详见 [LICENSE](LICENSE)。
