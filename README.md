# 厨具 VOC 洞察与闭环 Agent

将一个产品的客户反馈通过飞书机器人提交，后端使用 LangGraph 编排 Agent 分类与统计，直接在原飞书会话返回产品分析摘要和反馈类型饼图。支持 Excel、CSV、JSON 和飞书多维表格数据。

可直接上传测试的单产品 JSON：[product_feedback_demo.json](./data/product_feedback_demo.json)，包含 10 条模拟反馈。原有 [mock_feedback_50.csv](./mock_feedback_50.csv) 涉及多个产品，用于离线基线脚本；经机器人分析前应先筛选一个产品。

## 在飞书分析一个产品

1. 配置 `.env` 中的飞书应用凭据与 DeepSeek 凭据，启动长连接：

   ```bash
   uv pip install --python .venv/bin/python -r requirements.txt
   .venv/bin/python -m app.feishu_long_connection
   ```

2. 向机器人发送以下任一种输入。在群聊中需 @机器人。

   | 输入 | 提交方式 |
   | --- | --- |
   | Excel / CSV | 将 `.xlsx`、`.xls` 或 `.csv` 作为附件发送，Excel 读取第一个工作表 |
   | JSON 文件 | 将 `.json` 作为附件发送 |
   | JSON 消息 | 直接发送下面的 JSON 对象或反馈对象数组，也支持 JSON 代码块 |
   | 飞书多维表 | 发送目标数据表或已筛选单产品视图的链接，链接需包含 `table=tbl…`，可包含 `view=vew…` |

   ```json
   {
     "product": "CJ-001 不粘炒锅",
     "feedbacks": [
       {"text": "使用两周后锅底开始粘锅", "channel": "电商评论"},
       {"text": "说明书没写是否可以使用洗碗机", "channel": "客服聊天"},
       {"text": "锅柄松动，有烫伤风险", "channel": "售后工单"}
     ]
   }
   ```

   多维表链接示例（替换为自己的链接）：

   ```text
   分析 https://你的企业.feishu.cn/base/应用标识?table=tbl数据表标识&view=vew视图标识
   ```

   多维表没有产品列时，可用 `分析 产品=CJ-001 链接` 指定产品。支持 `/base/…` 和指向多维表的 `/wiki/…` 链接。后台通过应用身份分页读取，不修改源表。需要为应用开通多维表读取权限，并授予其目标表访问权限；知识库链接还需要读取知识库节点权限。[读取记录接口](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list)

3. 机器人收到数据后返回条数与运行编号，完成后发送分析摘要和饼图。摘要包含产品、分类成功/失败/重复/待复核数量、类别占比、情感分布、前三项问题、典型反馈和建议动作。图片上传需要应用具备相应图片资源权限。[上传图片接口](https://open.feishu.cn/document/server-docs/im-v1/image/create)

### 数据字段与统计口径

| 字段 | 可用中文列名 | 要求 |
| --- | --- | --- |
| `text` | 反馈内容、客户反馈、内容、评价内容 | 必须存在；空白记录作为失败项记录 |
| `sku` | 产品、产品名称、商品名称、产品型号 | 建议填写；可由 JSON 的 `product` 或同批唯一产品补齐 |
| `feedback_id` | 反馈编号、反馈ID | 可选，缺失时生成；同批重复编号只计一次 |
| `channel` | 渠道、反馈渠道、来源 | 可选，缺失时使用导入渠道 |
| `created_at` | 反馈时间、时间、日期 | 可选，缺失时使用导入时间，不代表真实反馈发生时间 |
| `order_id` | 订单号 | 可选 |

每批只分析一个产品，多产品文件会提示先筛选。产品完全缺失时仍可生成初步报告，但明细标记为待复核。JSON 支持对象数组，以及 `product`/`sku` 加 `feedbacks`/`records` 的对象格式。Excel 和多维表使用同一套字段映射。

批量分析不会因为待复核项暂停：模型分类作为初步统计，报告和饼图会显示待复核数量。类别占比的分母为分类成功的记录数，包含待复核初步分类，排除失败和重复记录。每条反馈按一个主类别计数，百分比独立四舍五入后总和可能有 0.1% 左右误差。待复核或不可行动的记录不生成待执行 Issue。

报告持久化在 SQLite 的 `run_reports` 表，同时保存到 `artifacts/runs/<run_id>/analysis.json`、`analysis.md` 和 `feedback_types.png`。若摘要或图片发送失败，分析结果仍保留，单独记录投递状态。

可在本地另起仅用于查询的 API 进程（不要同时配置同一应用的 webhook 消费），读取报告和图片：

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/runs/<run_id>/report
```

饼图地址为 `/runs/<run_id>/chart`。这些查询接口目前仅用于本地联调，公开部署前应加入身份与会话权限校验。单条自然语言反馈仍兼容原流程，可能暂停等待人工复核；飞书复核恢复命令尚未接入。

第 1 版分类规则见 [config/taxonomy.yaml](./config/taxonomy.yaml)，人工标注基准集见 [data/benchmark_50.csv](./data/benchmark_50.csv)。

## 本地运行数据基线

安装依赖后，可以先运行确定性的数据处理链路：

```bash
pip install -r requirements.txt
python scripts/seed_mock.py
```

脚本会完成 mock 数据清洗、基准标签合并、类型统计，并在 `artifacts/mock_run/` 生成清洗结果、统计 JSON、运行日志和反馈类型饼图。当前脚本使用人工标注基准集代替 LLM 分类，便于先验证数据链路和图表口径。

分类评估可先离线运行规则预测器：

```bash
python scripts/evaluate_classifier.py --mode keyword
```

配置 `OPENAI_API_KEY` 后，将 `--mode` 改为 `llm`，即可使用 LangChain 结构化模型对 50 条人工标注集进行一级分类、子类和严重程度评估。

周报和压力测试：

```bash
python scripts/run_weekly_report.py --database artifacts/weekly_demo.db --end-date 2026-09-14 --seed-mock
python scripts/stress_test.py --copies 20
```

周报会生成周期内的反馈类型分布、重复投诉率、Top 问题和饼图；压力测试将 50 条 mock 数据扩展为 1000 条，验证清洗和聚合吞吐量。

## 启动飞书机器人接入层

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

将飞书事件订阅地址配置为 `/webhooks/feishu`。当前接入层已经支持 URL challenge、文本反馈、文件事件、运行指令识别、事件去重和健康检查；`FeishuClient` 已封装租户令牌、消息发送、文件下载和多维表记录 Upsert，配置凭证后即可连接真实飞书环境。

真实环境启动前，复制 [.env.example](./.env.example) 并配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_VERIFICATION_TOKEN` 和 `OPENAI_API_KEY`。未配置飞书凭证时，文本事件仍可用于本地接入测试，但文件下载和出站消息不会调用外部 API。

### 飞书机器人配置步骤

1. 在[飞书开放平台](https://open.feishu.cn/)创建“企业自建应用”，进入“添加应用能力”启用“机器人”，记录应用的 `App ID` 和 `App Secret`。应用发布并通过企业管理员审批后，机器人能力才会在企业中生效。[官方配置说明](https://www.feishu.cn/content/425524486655)
2. 在“权限管理”中申请消息相关权限：接收机器人所在会话的消息、以机器人身份发送消息，以及读取消息中的文件资源。权限名称会随飞书后台版本变化，请在权限搜索框中分别搜索“接收消息”“发送消息”“消息资源”。
3. 在“事件与回调 → 事件配置”中选择“将事件发送至开发者服务器”，请求地址填写：

   ```text
   https://你的公网域名/webhooks/feishu
   ```

   订阅“接收消息”事件（事件标识通常为 `im.message.receive_v1`）。飞书保存地址时会发送 `challenge`，项目会原样返回该值完成地址验证。事件订阅和请求地址的配置入口可参考[飞书官方事件订阅说明](https://www.feishu.cn/content/917351528780--3333)。
4. 在事件配置页复制 `Verification Token`，填入项目根目录的 `.env`：

   ```bash
   cp .env.example .env
   # 编辑 .env，填入真实值
   FEISHU_APP_ID=cli_xxx
   FEISHU_APP_SECRET=xxx
   FEISHU_VERIFICATION_TOKEN=xxx
   FEISHU_ENCRYPT_KEY=
   LLM_MODEL=deepseek-v4-flash
   OPENAI_BASE_URL=https://api.deepseek.com
   LLM_STRUCTURED_OUTPUT_METHOD=json_mode
   OPENAI_API_KEY=xxx
   VOC_DATABASE=voc.db
   ```

   启动前在当前终端加载变量：

   ```bash
   set -a
   source .env
   set +a
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
5. 将机器人添加到测试群，在群内发送一条文本反馈，例如：

   ```text
   SKU=CJ-001；问题：锅盖密封圈装不上，已经漏汤两次
   ```

   机器人会先返回受理状态，处理完成后向原会话发送结果。上传文件时，字段和格式要求见“在飞书分析一个产品”。

6. 用下面的命令检查服务是否启动：

   ```bash
   curl http://127.0.0.1:8000/healthz
   ```

   如果服务在本地运行，需要使用具备公网 HTTPS 地址的反向代理或隧道，将该地址映射到本地 `8000` 端口；飞书后台不能直接访问 `127.0.0.1`。

当前代码校验 `Verification Token`，但没有实现 `Encrypt Key` 加密事件的解密和签名校验，因此事件配置应先使用明文回调模式；启用加密模式前需要补充解密逻辑。机器人发送消息使用飞书的消息 API 和租户访问凭证，具体接口可参考[官方发送消息文档](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create)。

### 使用长连接模式

项目也支持通过飞书官方 Python SDK 建立 WebSocket 长连接。长连接模式不需要公网回调地址，适合本地开发和演示：

```bash
uv pip install --python .venv/bin/python -r requirements.txt
set -a
source .env
set +a
python -m app.feishu_long_connection
```

使用长连接时，在飞书应用的“事件与回调”中将订阅方式切换为“长连接模式”，继续订阅 `im.message.receive_v1`，然后发布应用。长连接进程和 FastAPI 回调进程二选一运行，不能同时消费同一个应用的同一事件，否则会产生重复处理。官方 SDK 的长连接入口和消息模型见[飞书 SDK 文档](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/doc/channel.md)。

## 主要解决的痛点

### 1. 客户反馈入口分散

反馈可能来自客服对话、平台评论、直播间和 Excel 导出文件。不同入口的数据格式不一致，信息容易遗漏，也难以形成统一统计。

项目将飞书机器人作为统一入口，支持发送单条文本反馈、Excel/CSV/JSON 批量数据和多维表链接，并为每次批处理生成可追踪的运行批次。

### 2. 人工整理和判断成本高

客服或品控需要逐条阅读反馈、判断问题类别、识别严重程度，再整理成表格。反馈量增大后，处理速度和判断标准难以保持一致。

项目使用 Python 完成清洗、去重和聚合，使用 LLM 输出固定 JSON，对产品质量、尺寸规格、功能兼容、物流损坏等问题进行结构化分类，并保留原文证据。

### 3. 问题异常发现不及时

单条反馈本身很难看出趋势。某个 SKU 的投诉在短时间内集中增加时，人工汇总往往已经滞后。

项目按 SKU、日期、渠道、问题类别和严重程度统计反馈量，识别 Top 问题和反馈暴增事件。例如发现某 SKU 最近 7 天“涂层粘锅”反馈明显上升。

系统还会按一级问题类别汇总反馈数量和占比，生成反馈类型分布饼图，帮助业务人员快速判断投诉主要集中在哪些方面；“其他/待人工确认”保留为独立类别，避免统计失真。

### 4. 发现问题后缺少责任闭环

分析结果如果只停留在报告里，无法保证问题有人处理、按时处理，也无法确认处理后是否改善。

项目将可行动且不需要复核的分类结果聚合为本地 Issue 候选，保存问题标题、典型证据、建议动作和建议负责人。当前飞书主输出是分析摘要和饼图；问题单分派、处理状态回写属于后续协同能力。

### 5. 自动化结果缺少安全边界和可追溯性

涉及赔付、安全事故或低置信度判断时，直接自动执行可能带来业务风险；没有运行记录也难以定位错误来源。

项目保留低置信度和信息不足结果的复核标记，并在批量报告中披露。单条反馈仍使用 LangGraph 中断等待复核，面向飞书的复核恢复入口尚未接入。同时保存运行批次、模型结果和错误信息。

## 核心流程

```text
飞书提交单产品 Excel / JSON / 多维表链接
  → 长连接或 webhook 受理，后台下载附件或分页读取多维表
  → 中英文列名映射、产品校验、清洗、去重与脱敏
  → LangGraph 调用 DeepSeek，逐条输出分类与建议
  → 标记待复核项，隔离失败项
  → Python 计算类别占比、情感分布和主要问题
  → 生成分析摘要与反馈类型饼图
  → 保存报告，回传原飞书会话
```

## 技术分工

- **飞书机器人**：统一接收反馈、批量文件和运行指令，返回受理状态和处理结果。
- **FastAPI**：接收飞书事件、验签、下载文件并启动任务。
- **LangGraph**：编排状态图，管理节点、分支、重试、人工暂停/恢复和 checkpoint。
- **LangChain**：封装模型调用、结构化输出、Prompt 和模型切换。
- **Python 3.12**：使用 pandas 和 pydantic 完成数据处理与结果校验。
- **飞书多维表**：作为客户反馈输入源，按指定数据表或视图读取。
- **SQLite 与文件目录**：保存反馈明细、Issue 候选、批次报告、饼图和投递状态。

## 项目边界

首版面向面试作品 Demo，重点展示“反馈发现异常 → 定位 SKU → 分派处理 → 复盘趋势”的闭环。系统只做分析与协同，不自动改价、退款或修改商品页面。

详细的业务实施方案见 [落地方案.md](./落地方案.md)，可执行的工程设计见 [技术方案.md](./技术方案.md)。
