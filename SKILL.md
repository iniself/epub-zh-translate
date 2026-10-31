---
name: epub-zh-translate
description: 使用本地 epubzh 流水线把整本 EPUB 翻译成简体中文，处理术语表、目录、纯文本 pre 块、分块翻译、构建和校验。用户要求翻译 EPUB、继续 EPUB 翻译项目或检查译文时使用。
---

# EPUB 中文翻译

## 一、书籍信息

开始前先向用户确认以下信息。缺少必填项时直接询问，不要猜路径，也不要沿用上一个项目的书名或输出路径：

- 源文件（必填）：文件名即可，例如 `mybook.epub`；也可以给相对或绝对路径
- 中文书名（必填）：《……》
- 原书名（建议填）：只用于翻译规范，不参与找文件
- 作者：……
- 输出文件（必填）：文件名即可，例如 `mybook-zh.epub`；也可以给相对或绝对路径
- 项目目录（可选）：不填时按输出文件推导为
  `<输出文件所在目录>/<输出文件名去掉 .epub>-project`
- 分块配置（可选）：`large`（默认）或 `small`

用户只需要给文件名。**不要要求用户提供绝对路径**，也不要因为用户只写了文件名就反问。

项目目录就是 `book.toml` 所在目录。`book.toml`、`work/`、`src/` 都放在
项目目录下；最终 EPUB 仍在用户指定的输出路径。不要把 `book.toml` 生成在
OpenCode 的启动目录里。

### 路径怎么填

以用户启动 opencode 的目录为**会话目录**，它是所有相对路径的唯一基准。
**不要用命令的当前目录**：agent 一旦 `cd`，相对路径的含义就变了。

- 最佳实践：源文件和输出文件都放在会话目录，prompt 里只写文件名。
- 绝对路径原样使用；相对路径一律相对会话目录，与命令在哪执行无关。
- 调用 `init` 时把会话目录传给 `--base`，相对参数（`--config`、`--source`、
  `--output`）由工具用它拼接。`--work`、`--source-dir` 相对项目目录。
- 三种混合写法都必须支持：

| 源文件 | 输出 | 结果 |
| --- | --- | --- |
| 绝对路径 | 文件名 | 源用绝对路径；输出和项目目录在会话目录下 |
| 文件名 | 绝对路径 | 源在会话目录找；输出和项目目录在绝对路径所在目录下 |
| 绝对路径 | 绝对路径 | 都原样使用 |

`--source` 只给文件名、又不在会话目录时，工具会自动去输出目录（项目目录的上一级）
找；仍找不到就报错并列出搜过的目录，**不要换目录猜**。

`init` 会打印每个参数的解析结果，必须核对：

```text
base    = /path/to/session
config  = mybook-zh-project/book.toml -> /path/to/session/mybook-zh-project/book.toml
source  = mybook.epub                 -> /path/to/session/mybook.epub
output  = mybook-zh.epub              -> /path/to/session/mybook-zh.epub
```

如果 `config` 解析到了会话目录、而不是 `*-project/`，立刻停下重来，不要继续
`extract`——否则 `work/`、`src/` 会建在错误的位置。

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

用户调用模板（用户只写文件名，不需要绝对路径）：

```text
@epub-zh-translate

源文件：mybook.epub
中文书名：中文书名
原书名：Original Title
作者：Author
输出：mybook-zh.epub
分块配置：small
```

收到这段 prompt 后，agent 自己完成：会话目录取 OpenCode 的启动目录；源文件、输出都按
会话目录解析；项目目录取 `会话目录/mybook-zh-project`；再按“路径怎么填”一节的规则
拼出 `init --base ...` 的绝对参数。**不要把这些路径问题转嫁给用户。**

开始执行前，先复述收集到的参数（含实际生效的分块配置）和将要运行的命令，得到用户确认后再动手。

原书名只用于翻译规范；用户不提供时不要编造。`init` 后检查 `book.toml` 的 `source_title`，避免它被中文书名兜底；必要时修正。

## 二、目标语言与排版

- 目标语言：简体中文
- 源语言：目前只支持使用拉丁字母、以空格分词的语言（英语、法语、德语、西班牙语等）。
  日语、韩语、泰语等无空格语言，以及俄语、希腊语、阿拉伯语等非拉丁字母语言暂不支持；
  遇到这类书先向用户说明限制，不要直接开始。
- 中英文之间加一个半角空格；中文使用全角标点。
- 使用工具默认中文排版 CSS（字体栈、行高 1.75、两端对齐、代码块样式）。
- 代码、命令、URL、版本号内部保持半角。

如果用户要求简体中文以外的语言，先说明：当前空格规则和 CSS 针对中文优化，换成日文、韩文等需要额外调整，不要直接套用。

## 三、流程与质量门槛

1. 如果项目目录没有 `book.toml`：先 `init` + `extract`，确认所有文件 round-trip 通过后再开始翻译。
   如果已有 `book.toml`：先运行 `next`，从它给出的那一块继续；需要看总进度时再
   运行 `status`。**不要先跑全项目 `check`**，未开始的分块会刷屏。不要覆盖已有译文。
2. 阅读目录、前言和前两章（按书里实际存在的内容）：有 `work/toc.tsv` 就补译其中的标题；
   没有就跳过，不要手工创建——它只从原书的 NCX / 导航文档生成。没有前言就从正文第一章开始，
   不足两章按实际章节数读；封面、版权页、目录页不算正文第一章。然后完善 `work/terms.tsv`，
   运行 `guide`；它会生成 `work/TRANSLATION_GUIDE.md`。
3. 正文按分块翻译，流程见下面的“分块翻译”；每个分块写完后用 `check --chunk --fix` 自检。同一章的全部分块完成后运行 `check <章名>`，要求该章清零（段数、占位符、未翻译、中英文空格）。
4. 全部完成后运行 `build` 和 `verify`；XML、内部链接、锚点、占位符必须零错误。构建前运行一次
   `terms-audit`，检查术语一致性（默认只报告，不阻断）。
5. 不要修改源 EPUB；不要手工改 `work/build` 里的文件，所有修改通过 `work/zh/`、`work/toc.tsv`、`work/pre.json`、`work/terms.tsv` 完成。

### 分块翻译（照做，不要自己发明流程）

每个分块 = 一个 subagent = 一次 `write`。主会话只做调度和校验，**不要在主会话里直接翻译整块**。

```bash
# macOS / Linux
TOOL="<skill-base>/epubzh-run"
# Windows PowerShell: $TOOL = "<skill-base>\epubzh-run.cmd"
# Windows 上调用写成 & $TOOL，不要写 "$TOOL"

# 1. 选择下一块（默认按阅读顺序；一次只给一块）
"$TOOL" next --config book.toml

# 只有用户要求“从上次中断处继续/先修复坏块”时才加 --resume
"$TOOL" next --config book.toml --resume

# 2. 需要看总进度时才运行 status
"$TOOL" status --config book.toml

# 3. 生成该分块的提示词（写到 work/prompts/<stem>.<nn>.md）
"$TOOL" prompt <stem> --chunk <n>

# 4. 把提示词文件路径交给一个 subagent，让它读文件并执行
#    一个分块一个 subagent；不要复制粘贴提示词正文

# 5. 只校验这一块（--fix 自动修复机械错误）
"$TOOL" check --config book.toml <stem> --chunk <n> --fix
```

### 继续已有项目（包括小模型）

用户只给项目目录时，不要自己重新规划整本书，也不要先跑全项目管理命令。按这个循环走：

1. `next --config <book.toml>`：它只返回一个分块，并生成对应的 prompt 文件；
2. 读 `next` 打印的 prompt 路径，按执行契约只处理这一块；
3. `check <stem> --chunk <n> --fix`；
4. 通过后再运行一次 `next`，直到没有下一块。

`next` 的输出里，`chunk 2 (15 segments)` 表示“第 2 块，共 15 段”，不是
“第 2 块，共 15 块”；全书块数进度看 `progress: 11/128 chunks`。返回
`partial: 1/15 segments done` 时，按 `action:` 行从第 2 段续写，**不要用
`--resume`**。

`next` 默认严格按阅读顺序返回第一个不是 `done` 的分块；后面的 broken/partial 不会插队。
只有用户明确说“从上次中断处继续”或“先修复坏块”时，才用 `next --resume`，它优先返回
repair / partial / broken，再按顺序补 pending。所以不需要先跑全项目 `check`。项目级
`check` 默认跳过还没有译文文件的分块；只有最终验收时才加 `--all` 要求所有分块都存在。
`check <stem>` 仍会严格检查该文件的所有分块。

硬规则：

- 分块提示词必须用 `prompt` 生成，不要自己拼；`large` 带完整规范，`small` 带规则卡 +
  本块术语 + 上文。
- 一个 subagent 只做一个分块，不要让它顺手翻译别的分块。
- 子代理如果反复说“我将翻译”“Actually, I'll just translate it”却不动手，说明它没有按
  提示词执行：重发一句“读 `<prompt 文件>`，按执行契约直接写文件，不要回复计划”，
  不要追加解释，也不要换一种说法反复劝。
- 继续已有项目时，第一条命令是 `next`，不是 `status`，更不是项目级 `check`。`next`
  一次只给一个分块；处理完再 `next`。
- `next` 的 `action:` 行已经写明当前该做什么（续写、修复或新译），直接照做；
  不要在 `next` 和 `next --resume` 之间反复犹豫。
- 占位符编号是**每段独立**的：不同段可以重复 `⟦0⟧`、`⟦1⟧`、`⟦2⟧`。不要跨段比较
  编号，也不要为了“全局唯一”改编号。
- 成对占位符必须保持源文顺序：开标记放在对应译文前，闭标记紧跟对应译文后，
  **不要把闭标记集中到段尾**；只有单独占位符可以随语序移动。
- 同一段同一个错误连续 2 次 `check` 仍出现，停止派发，把该段号和报错报告给用户；
  不要继续猜。同一分块最多 3 轮 `check --chunk --fix`，仍不过就停止。
- `check --chunk --fix` 会先做确定性的修复：
  - 段号越界的块直接删除；
  - 重复段块中只有一份占位符正确时保留那一份，多份内容相同时保留第一份；
  - 多份都不同且无法判断时保留最后一份并打印 `kept the last copy` 警告；
  - 段号完整但顺序错误时按段号排序；
  - 源文是单独标记 `⟦n⟧`、译文多出 `⟦/n⟧` 时，直接删除这个多余闭合标记。
    成对的多余标记仍只报告，不自动猜删哪一对。
- `check --chunk` 的报错会带 `src:` 和 `got:` 两行；占位符类错误给全文，不截断。
  段号写成 `seg 8/40`，对应文件里的 `@@@SEG 008@@@`，按这一段只改那一行，不要
  通读整块。报 `segment markers` 时会点名重复/缺失段号和重复段所在行号；缺中间段
  会给 `insert between @@@SEG 00x@@@ and @@@SEG 00y@@@`，必须插入指定位置，
  **不要追加到文件末尾**。
- 子代理执行契约要求**优先一次 `write` 写完**整个分块，只有写被截断才追加。追加
  时 oldString 只取最后一段，newString 不得重复最后一段——重复段几乎都来自这个
  边界写错。`edit` 返回 identical/找不到/多处匹配时不得重试，直接 `write` 重写整块。
- `status` 里的 `Np` 是 partial：重新 `prompt` 会给出“从第 N+1 段续写”的指令，
  只追加、不要整块重写。`Nb` 是 broken（段号不连续）：先运行
  `check <stem> --chunk <n> --fix`，能修好就继续；仍 broken 再整体重写该块。
- 同一章所有分块完成后，再跑一次整章 `check <stem>` 要求清零；此时剩下的才是真问题，
  不会再有“别的分块缺失”。
- 第一章定稿后写 `work/style.md`（10–15 组英中例句 + 硬决定：人称、术语首次出现格式、
  数字与引号），它是后续所有分块的风格基准。
- 每章清零后，把本章的新译法、句式取舍追加到 `work/decisions.md`，后续分块会自动带上。

#### 主会话看门狗（防止弱模型空转）

1. 子代理返回后，主会话**必须再跑一次** `check --chunk <n> --fix`；不要相信子代理
   回复的“已完成”。
2. 子代理没有写输出文件、只回复计划、或长时间没有动作：中断它，重发一次
   “读 `<prompt>`，直接写文件，不要回复计划”，不要追加解释。
3. 同一分块最多派发 2 次；第 2 次仍不过，停止派发并把具体报错报告给用户。
4. 子代理的完成回复应为 `已写入 <path>；check OK` 或 `失败：<原因>`；没有 `check OK`
   就按未完成处理。

## 四、内容处理策略

- 代码块：保持英文原样。`<pre>` 之外的代码块（发布方用 `<div class="ProgramCode">`
  之类自定义类排版）在 `book.toml` 的 `[extract] protect_blocks = ["div.ProgramCode"]`
  里声明，抽取时整块冻结、不进入翻译队列；元素名不固定，按书检查后填写。
- 未转义的 `>`：部分出版社在正文或属性值里留了裸 `>`（例如 `score > 0.8`），
  XML 合法但会让回环校验失败。`unpack`/`extract` 会自动把这类 `>` 转义成
  `&gt;`（XML 等价，渲染结果不变），`<pre>` 和 `protect_blocks` 里的内容不动。
  不需要手工处理，也不需要额外脚本。
- 夹在 `<p>` 里的零星代码行无法自动识别：翻译时原样抄回，并在该段内单独加一行
  `@@@VERBATIM@@@`（段首或段尾都可以），`check` 就不会把它算成“未翻译”。
- 行内代码、命令、API 名称、库名、类名、函数名、参数名、文件名、URL：保持英文。
- 目录：保留原书章节编号和锚点，标题译成中文；原书确有、但自动映射不到的标题，在
  `work/toc.tsv` 已生成时补行。`work/toc.tsv` 只从 NCX / 导航文档生成，没有就不要手工
  创建。它同时供 EPUB2 的 `toc.ncx` 和 EPUB3 的导航文档（`package.opf` 中
  `properties="nav"` 的文件）使用，一次翻译两处生效；页码列表保持数字不动。
- 图注、表注、脚注、提示框：翻译；编号保持“图 4.1 / 表 2.1 / 清单 4.1”。
- 表格正文翻译；表头、单元格中的代码、命令、参数保持英文。
- 索引：翻译条目说明，保留字母分组和链接锚点。
- 字母分组标题保持单个字母（A、B、C…），不要写成“字母 A”。

## 五、语言与术语

- 风格：通俗易懂、技术准确，像资深工程师给同事讲课；保留原文的例子、类比和幽默感。
- 人称：you → 你；we → 我们。
- 术语：首次出现写“中文（English）”，之后统一用中文；以 `work/terms.tsv` 为准。
- 人名/地名：正文中音译，首次出现附原文；作者、机构、地址等元数据可保留英文原名；产品名、公司名、协议名保留英文。
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

工具入口是包含本 `SKILL.md` 的目录下的 `epubzh-run`；Windows 上是
`epubzh-run.cmd`。skill 加载时会给出基础目录（Base directory）；优先用这个
基础目录拼接。拿不到基础目录时，工具就在 `SKILL.md` 所在目录。不要假设书籍
项目根目录（`book.toml` 所在目录）里有 `./epubzh-run`。

macOS / Linux 用 `"$TOOL"` 调用；Windows PowerShell 用 `& $TOOL` 调用，
不要写 `"$TOOL"`。下面的命令以 macOS / Linux 为例。

不要 `cd` 到项目目录，也不要在命令里使用依赖当前目录的相对配置。所有命令都用
绝对 `--config`，并在 `init` 时用 `--base` 明确会话目录。项目目录按输出文件
推导；用户显式给了项目目录时，以用户给的为准：

```bash
TOOL="<skill-base>/epubzh-run"
# Windows PowerShell: $TOOL = "<skill-base>\epubzh-run.cmd"
# Windows 直接拼绝对路径，不要照抄下面的 case/dirname 写法。
SESSION="/path/to/books"      # 用户启动 opencode 的目录
SOURCE="mybook.epub"          # 在会话目录时只写文件名
OUTPUT="mybook-zh.epub"       # 在会话目录时只写文件名
TITLE="中文书名"
SOURCE_TITLE="Original Title"
AUTHOR="Author"

# 输出是绝对路径时项目目录跟着它；否则按会话目录推导
case "$OUTPUT" in
  /*) OUTPUT_ABS="$OUTPUT" ;;
  *)  OUTPUT_ABS="$SESSION/$OUTPUT" ;;
esac
PROJECT="$(dirname "$OUTPUT_ABS")/$(basename "$OUTPUT_ABS" .epub)-project"
CONFIG="$PROJECT/book.toml"

"$TOOL" init \
  --base "$SESSION" \
  --config "$CONFIG" \
  --source "$SOURCE" \
  --title "$TITLE" \
  --source-title "$SOURCE_TITLE" \
  --author "$AUTHOR" \
  --output "$OUTPUT" \
  --profile large

"$TOOL" extract --config "$CONFIG"
"$TOOL" guide --config "$CONFIG"
"$TOOL" next --config "$CONFIG"
"$TOOL" status --config "$CONFIG"
"$TOOL" prompt <stem> --chunk <n> --config "$CONFIG"
"$TOOL" check <stem> --chunk <n> --config "$CONFIG"
"$TOOL" check --config "$CONFIG"
"$TOOL" check --config "$CONFIG" --all
"$TOOL" terms-audit --config "$CONFIG"
"$TOOL" build --config "$CONFIG"
"$TOOL" verify --config "$CONFIG"
```

固定流程：

`init → extract → guide → next/分块翻译 → 逐章 check → terms-audit → build → verify`

规则：

- 不要 `cd`，`--config` 永远传绝对路径。`init` 必须给 `--base`（所有路径都是绝对
  路径时可以省略），相对路径一律相对会话目录，不相对当前目录。
- `init` 打印的 `config` / `source` / `output` 解析结果必须核对；`config` 落在会话
  目录而不是 `*-project/` 时立刻停下重来。
- `--source` 找不到时，工具会列出搜过的目录和候选 `.epub`；按提示把书放到输出目录
  或修正路径，不要换目录反复试。
- 新项目先 `init` + `extract`；已有 `book.toml` 时先 `next`，从它给出的那一块继续，
  不要先跑全项目 `check`，也不要覆盖已有译文。
- `--profile` 只在 `init` 时决定（`large`/`small`）。已有译文后用 `extract --profile`
  切换会被拒绝；`--force` 会重切分块，等于要求重译，除非用户明确同意，不要使用。
- `next` 默认按阅读顺序返回第一个不是 `done` 的分块，并生成对应 prompt；它不会修改
  译文。用户要求从上次中断处继续时用 `next --resume`，它先返回 repair / partial /
  broken，再按顺序补 pending。
- `check <章名>` 按文件严格检查所有分块；`check <章名> --chunk <n>` 只检查一个分块，
  子代理自检用后者。项目级 `check` 默认跳过尚未开始的分块，最终验收用
  `check --all` 要求所有分块都存在。
- `status` 里的 `Np` 是 partial、`Nb` 是 broken；`prompt` 会给出续写或重写指令。
  如果分块段号完整但自检有错，`prompt` 会标记为 repair，要求先修复而不是重写。
- `prompt` 默认把提示词写到 `work/prompts/<stem>.<nn>.md`，终端只打印路径；
  需要看内容时加 `--stdout`。
- `build` 每次都从源 EPUB 重新复制并重建 `work/build`；不要手工修改
  `work/build`。
- `verify` 检查最终 EPUB 的结构、链接、锚点和占位符；它不替代 `epubcheck`。
- 参数不确定时运行 `"$TOOL" <命令> --help`，不要凭记忆猜参数。
- 不要修改源 EPUB；译文只写入 `work/zh/`，目录、`<pre>`、术语分别通过
  `work/toc.tsv`、`work/pre.json`、`work/terms.tsv` 维护。
- 这些命令由 agent 执行，不要让用户手动运行。
