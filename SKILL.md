---
name: EPUB 中文翻译
description: 使用本地 epubzh 流水线把整本 EPUB 翻译成简体中文，处理术语表、目录、纯文本 pre 块、分块翻译、构建和校验。用户要求翻译 EPUB、继续 EPUB 翻译项目或检查译文时使用。
---

# EPUB 中文翻译

## 一、书籍信息

开始前先向用户确认以下信息。缺少必填项时直接询问，不要猜路径，也不要沿用上一个项目的书名或输出路径：

- 源文件（必填）：用于定位和解包书籍的 `.epub` 路径
- 中文书名（必填）：《……》
- 原书名（建议填）：只用于翻译规范，不参与找文件
- 作者：……
- 输出文件（必填）：最终 EPUB 的完整路径
- 项目目录（可选）：不填时按输出文件推导为
  `<输出文件所在目录>/<输出文件名去掉 .epub>-project`
- 分块配置（可选）：`large`（默认）或 `small`

项目目录就是 `book.toml` 所在目录。`book.toml`、`work/`、`src/` 都放在
项目目录下；最终 EPUB 仍在用户指定的输出路径。不要把 `book.toml` 生成在
OpenCode 的启动目录里。

例如：

```text
输出文件：/path/to/books/mybook-zh.epub
项目目录：/path/to/books/mybook-zh-project
book.toml：/path/to/books/mybook-zh-project/book.toml
work：/path/to/books/mybook-zh-project/work
```

如果用户已经给出这些信息，直接使用，不要重复询问。

分块配置决定每个翻译分块的大小，**必须在 `init` 之前确定**，翻译开始后不能改：

- `large`（默认，等同历史行为）：约 2600 词、不限段数，适合输出上限 ≥8k 的大模型；
- `small`：约 1200 词、最多 40 段，适合上下文或输出受限的模型，也用于之前翻译卡住过的书。

用户通常在调用时写一行 `分块配置：small`；也识别 `模型：small` 这种写法。没写就是
`large`。把它映射成 `init --profile <值>`，非法值必须停下来问，不要回落到默认值。
`init` 之后读回 `book.toml` 的 `[extract] profile` 并复述，防止说 small 实际跑 large。

用户调用模板：

```text
@epub-zh-translate

源文件：/path/to/book.epub
中文书名：中文书名
原书名：Original Title
作者：Author
输出：/path/to/book-zh.epub
分块配置：small
```

开始执行前，先复述收集到的参数（含实际生效的分块配置）和将要运行的命令，得到用户确认后再动手。

原书名只用于翻译规范；用户不提供时不要编造。`init` 后检查 `book.toml` 的 `source_title`，避免它被中文书名兜底；必要时修正。

## 二、目标语言与排版

- 目标语言：简体中文
- 中英文之间加一个半角空格；中文使用全角标点。
- 使用工具默认中文排版 CSS（字体栈、行高 1.75、两端对齐、代码块样式）。
- 代码、命令、URL、版本号内部保持半角。

如果用户要求简体中文以外的语言，先说明：当前空格规则和 CSS 针对中文优化，换成日文、韩文等需要额外调整，不要直接套用。

## 三、流程与质量门槛

1. 如果项目目录没有 `book.toml`：先 `init` + `extract`，确认所有文件 round-trip 通过后再开始翻译。
   如果已有 `book.toml`：先运行 `status` + `check`，确认是续跑还是新项目；不要覆盖已有译文。
2. 阅读目录、前言和前两章，完善 `work/terms.tsv`，然后运行 `guide`；它会生成 `work/TRANSLATION_GUIDE.md`。
3. 正文按分块翻译，流程见下面的“分块翻译”；每个分块写完后用 `check --chunk` 自检。同一章的全部分块完成后运行 `check <章名>`，要求该章清零（段数、占位符、未翻译、中英文空格）。
4. 全部完成后运行 `build` 和 `verify`；XML、内部链接、锚点、占位符必须零错误。构建前运行一次
   `terms-audit`，检查术语一致性（默认只报告，不阻断）。
5. 不要修改源 EPUB；不要手工改 `work/build` 里的文件，所有修改通过 `work/zh/`、`work/toc.tsv`、`work/pre.json`、`work/terms.tsv` 完成。

### 分块翻译（照做，不要自己发明流程）

每个分块 = 一个 subagent = 一次 `write`。主会话只做调度和校验，**不要在主会话里直接翻译整块**。

```bash
TOOL="<skill-base>/epubzh-run"

# 1. 看还剩哪些分块（partial / broken 会标出来）
"$TOOL" status --config book.toml

# 2. 生成该分块的提示词（写到 work/prompts/<stem>.<nn>.md）
"$TOOL" prompt <stem> --chunk <n>

# 3. 把提示词文件路径交给一个 subagent，让它读文件并执行
#    一个分块一个 subagent；不要复制粘贴提示词正文

# 4. 只校验这一块
"$TOOL" check --config book.toml <stem> --chunk <n>
```

硬规则：

- 分块提示词必须用 `prompt` 生成，不要自己拼；`large` 带完整规范，`small` 带规则卡 +
  本块术语 + 上文。
- 一个 subagent 只做一个分块，不要让它顺手翻译别的分块。
- 子代理如果反复说“我将翻译”“Actually, I'll just translate it”却不动手，说明它没有按
  提示词执行：重发一句“读 `<prompt 文件>`，按执行契约直接写文件，不要回复计划”，
  不要追加解释，也不要换一种说法反复劝。
- 同一分块连续 3 次 `check --chunk` 不过，停下来报告用户，不要继续重试。
- `status` 里的 `Np` 是 partial：重新 `prompt` 会给出“从第 N+1 段续写”的指令，
  只追加、不要整块重写。`Nb` 是 broken（段号不连续）：按提示词整体重写该块。
- 同一章所有分块完成后，再跑一次整章 `check <stem>` 要求清零；此时剩下的才是真问题，
  不会再有“别的分块缺失”。
- 第一章定稿后写 `work/style.md`（10–15 组英中例句 + 硬决定：人称、术语首次出现格式、
  数字与引号），它是后续所有分块的风格基准。
- 每章清零后，把本章的新译法、句式取舍追加到 `work/decisions.md`，后续分块会自动带上。

## 四、内容处理策略

- 代码块：保持英文原样。`<pre>` 之外的代码块（发布方用 `<div class="ProgramCode">`
  之类自定义类排版）在 `book.toml` 的 `[extract] protect_blocks = ["div.ProgramCode"]`
  里声明，抽取时整块冻结、不进入翻译队列；元素名不固定，按书检查后填写。
- 未转义的 `>`：部分出版社在正文或属性值里留了裸 `>`（例如 `score > 0.8`），
  XML 合法但会让回环校验失败。`unpack`/`extract` 会自动把这类 `>` 转义成
  `&gt;`（XML 等价，渲染结果不变），`<pre>` 和 `protect_blocks` 里的内容不动。
  不需要手工处理，也不需要额外脚本。
- 夹在 `<p>` 里的零星代码行无法自动识别：翻译时原样抄回，并在该段标记下一行
  加 `@@@VERBATIM@@@`，`check` 就不会把它算成“未翻译”。
- 行内代码、命令、API 名称、库名、类名、函数名、参数名、文件名、URL：保持英文。
- 目录：保留原书章节编号和锚点，标题译成中文；自动映射不到的标题补进 `work/toc.tsv`。
  `work/toc.tsv` 同时供 EPUB2 的 `toc.ncx` 和 EPUB3 的导航文档（`package.opf` 中
  `properties="nav"` 的文件）使用，一次翻译两处生效；页码列表保持数字不动。
- 图注、表注、脚注、提示框：翻译；编号保持“图 4.1 / 表 2.1 / 清单 4.1”。
- 表格正文翻译；表头、单元格中的代码、命令、参数保持英文。
- 索引：翻译条目说明，保留字母分组和链接锚点。
- 字母分组标题保持单个字母（A、B、C…），不要写成“字母 A”。

## 五、语言与术语

- 风格：通俗易懂、技术准确，像资深工程师给同事讲课；保留原文的例子、类比和幽默感。
- 人称：you → 你；we → 我们。
- 术语：首次出现写“中文（English）”，之后统一用中文；以 `work/terms.tsv` 为准。
- 人名/地名：音译，首次出现附原文；产品名、公司名、协议名保留英文。
- 术语表没有的译法，先补进 `work/terms.tsv` 再继续。
- `terms.tsv` 用三列：`English`、`规范中文`、`备注或禁用变体`（可留空）。第二列只写译名，
  不要把“首次出现写……”这类说明写进去；说明放第三列，或写进 `work/decisions.md`。
- 每章 `check` 清零后运行 `terms-audit`，看哪些术语用词不一致。它默认只报告、不阻断；
  `--strict` 才返回非零。合理变体补进第三列，确认漏译再改 `work/zh/`。
- 新补的术语会影响已翻章节，用 `terms-audit --only <文件名>` 回扫，不要整本重翻。

## 六、翻译与成稿选项

以下为默认设置，请按此执行；用户明确覆盖时，以用户本次要求为准：

- 代码注释：不翻译
- 纯文本 `<pre>`：只翻译 `work/pre.json` 中确认是自然语言的块（LLM 对话、问答示例、模型输出）；代码、命令、日志、配置以及含标签的块保持原文
- 术语首次出现：中文（English）
- 人名地名：音译，首次出现附原文
- 目录编号：保留
- 目录页码：保留
- 输出文件名：以第一节的输出路径为准；如要带副标题，请确保该路径本身包含副标题
- 目标语言：简体中文

## 七、验收与汇报

- 抽查：封面、目录、一章正文、一个表格、一个代码块、索引页。
- 汇报：输出路径、分块配置（large/small）、总块数/完成数、`check` 结果、`terms-audit`
  结论、`verify` 结果、遗留问题、抽查结论。

## 八、工具调用契约

工具入口是包含本 `SKILL.md` 的目录下的 `epubzh-run`。skill 加载时会给出
基础目录（Base directory）；优先用这个基础目录拼接。拿不到基础目录时，
工具就在 `SKILL.md` 所在目录。不要假设书籍项目根目录（`book.toml`
所在目录）里有 `./epubzh-run`。

所有命令都在书籍项目根目录（`book.toml` 所在目录）执行，并显式传入项目配置。
项目目录按输出文件推导；用户显式给了项目目录时，以用户给的为准：

```bash
TOOL="<skill-base>/epubzh-run"
SOURCE="/path/to/book.epub"
OUTPUT="/path/to/books/mybook-zh.epub"
TITLE="中文书名"
SOURCE_TITLE="Original Title"
AUTHOR="Author"

PROJECT="$(dirname "$OUTPUT")/$(basename "$OUTPUT" .epub)-project"
mkdir -p "$PROJECT"
cd "$PROJECT"

"$TOOL" init \
  --config "$PROJECT/book.toml" \
  --source "$SOURCE" \
  --title "$TITLE" \
  --source-title "$SOURCE_TITLE" \
  --author "$AUTHOR" \
  --output "$OUTPUT" \
  --profile large

"$TOOL" extract --config book.toml
"$TOOL" guide --config book.toml
"$TOOL" status --config book.toml
"$TOOL" prompt <stem> --chunk <n> --config book.toml
"$TOOL" check <stem> --chunk <n> --config book.toml
"$TOOL" check --config book.toml
"$TOOL" terms-audit --config book.toml
"$TOOL" build --config book.toml
"$TOOL" verify --config book.toml
```

固定流程：

`init → extract → guide → 分块翻译 → 逐章 check → terms-audit → build → verify`

规则：

- 新项目先 `init` + `extract`；已有 `book.toml` 时先 `status` + `check`，
  不要覆盖已有译文。
- `--profile` 只在 `init` 时决定（`large`/`small`）。已有译文后用 `extract --profile`
  切换会被拒绝；`--force` 会重切分块，等于要求重译，除非用户明确同意，不要使用。
- `check <章名>` 按文件检查；`check <章名> --chunk <n>` 只检查一个分块，子代理自检用后者。
- `status` 里的 `Np` 是 partial、`Nb` 是 broken；`prompt` 会给出续写或重写指令。
- `prompt` 默认把提示词写到 `work/prompts/<stem>.<nn>.md`，终端只打印路径；
  需要看内容时加 `--stdout`。
- `build` 每次都从源 EPUB 重新复制并重建 `work/build`；不要手工修改
  `work/build`。
- `verify` 检查最终 EPUB 的结构、链接、锚点和占位符；它不替代 `epubcheck`。
- 参数不确定时运行 `"$TOOL" <命令> --help`，不要凭记忆猜参数。
- 不要修改源 EPUB；译文只写入 `work/zh/`，目录、`<pre>`、术语分别通过
  `work/toc.tsv`、`work/pre.json`、`work/terms.tsv` 维护。
- 这些命令由 agent 执行，不要让用户手动运行。
