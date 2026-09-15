# 厨具 VOC 洞察与闭环 Agent

这是一个面向厨具业务的客户反馈分析与协同 Demo。项目通过飞书机器人统一接收客户反馈，使用 LangGraph 编排处理流程，自动完成反馈清洗、问题分类、类型分布可视化、趋势分析、责任人分派和处理结果复盘。

示例数据：[mock_feedback_50.csv](./mock_feedback_50.csv)。文件包含 50 条脱敏模拟反馈，字段与批量导入契约一致，可直接用于测试分类、趋势统计和反馈类型饼图；其中刻意加入了某 SKU 近期“涂层粘锅”反馈集中上升的演示事件。

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

   机器人会先返回受理状态，处理完成后向原会话发送结果。上传 CSV/XLSX 文件时，文件必须包含 `feedback_id`、`text`、`sku`、`channel`、`created_at` 五个字段。

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

项目将飞书机器人作为统一入口，支持发送单条文本反馈或上传 Excel/CSV 批量数据，并为每次批处理生成可追踪的运行批次。

### 2. 人工整理和判断成本高

客服或品控需要逐条阅读反馈、判断问题类别、识别严重程度，再整理成表格。反馈量增大后，处理速度和判断标准难以保持一致。

项目使用 Python 完成清洗、去重和聚合，使用 LLM 输出固定 JSON，对产品质量、尺寸规格、功能兼容、物流损坏等问题进行结构化分类，并保留原文证据。

### 3. 问题异常发现不及时

单条反馈本身很难看出趋势。某个 SKU 的投诉在短时间内集中增加时，人工汇总往往已经滞后。

项目按 SKU、日期、渠道、问题类别和严重程度统计反馈量，识别 Top 问题和反馈暴增事件。例如发现某 SKU 最近 7 天“涂层粘锅”反馈明显上升。

系统还会按一级问题类别汇总反馈数量和占比，生成反馈类型分布饼图，帮助业务人员快速判断投诉主要集中在哪些方面；“其他/待人工确认”保留为独立类别，避免统计失真。

### 4. 发现问题后缺少责任闭环

分析结果如果只停留在报告里，无法保证问题有人处理、按时处理，也无法确认处理后是否改善。

项目自动生成 Issue，包含问题标题、典型证据、建议动作、负责人和截止日期，并通过飞书通知相关人员。负责人更新状态后，系统在周报中比较问题量和重复投诉率的变化。

### 5. 自动化结果缺少安全边界和可追溯性

涉及赔付、安全事故或低置信度判断时，直接自动执行可能带来业务风险；没有运行记录也难以定位错误来源。

项目对低置信度、敏感问题和字段缺失的结果自动进入人工复核，通过 LangGraph 的暂停/恢复机制继续处理；同时记录原始反馈、运行批次、模型结果、错误信息和工作流版本。

## 核心流程

```text
飞书机器人接收文本或文件
  → FastAPI 接收事件并启动 LangGraph
  → Python 清洗、去重、脱敏
  → LLM 结构化分类
  → Schema 校验与失败重试
  → 低置信度或敏感问题人工复核
  → 反馈类型占比与趋势聚合
  → 生成反馈类型分布饼图和 Top 问题
  → 生成 Issue、建议动作和负责人
  → 写入飞书多维表并发送通知
  → 根据 Issue 状态生成周报复盘
```

## 技术分工

- **飞书机器人**：统一接收反馈、批量文件和运行指令，返回受理状态和处理结果。
- **FastAPI**：接收飞书事件、验签、下载文件并启动任务。
- **LangGraph**：编排状态图，管理节点、分支、重试、人工暂停/恢复和 checkpoint。
- **LangChain**：封装模型调用、结构化输出、Prompt 和模型切换。
- **Python 3.12**：使用 pandas 和 pydantic 完成数据处理与结果校验。
- **飞书多维表**：保存 Feedback、Issue、Taxonomy 和 Run Log。

## 项目边界

首版面向面试作品 Demo，重点展示“反馈发现异常 → 定位 SKU → 分派处理 → 复盘趋势”的闭环。系统只做分析与协同，不自动改价、退款或修改商品页面。

详细的业务实施方案见 [落地方案.md](./落地方案.md)，可执行的工程设计见 [技术方案.md](./技术方案.md)。
