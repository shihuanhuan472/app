# 系统文件处理与问答回复流程图

本文档用于说明当前系统从文件上传、解析、审核、向量化，到用户提问、检索、生成回复的完整工作流程，并标注每一步涉及的主要技术与代码模块。

## 总览图

```mermaid
flowchart LR
    user["用户<br/>浏览器前端"] --> frontend["前端页面<br/>static/*.html<br/>HTML + CSS + JS"]
    frontend --> api["后端 API<br/>FastAPI<br/>routers/*"]

    api --> fileStore["文件存储<br/>upload/source_documents<br/>upload/documents<br/>upload/images"]
    api --> mysql["业务数据库<br/>MySQL + SQLAlchemy asyncmy"]

    api --> parser["文档解析层<br/>utils/*Parser.py<br/>PyMuPDF / MinerU / python-pptx / BeautifulSoup / Selenium"]
    parser --> llmParse["结构化抽取<br/>OpenAI 兼容接口<br/>MODEL_AI 多模态大模型"]
    llmParse --> review["审核流<br/>document_reviews<br/>knowledge_document_reviews"]

    review --> docdb["正式文档库<br/>document_breakdowns<br/>document_knowledges<br/>knowledge_document_sections"]
    docdb --> vector["向量化服务<br/>VectorService<br/>VectorStoreMultimodal"]
    vector --> embed["多模态嵌入<br/>Visualized_BGE + BAAI/bge-m3<br/>PyTorch CPU/CUDA 自动选择"]
    embed --> milvus["向量数据库<br/>Milvus<br/>documents_collection_main_chunk"]

    api --> rag["问答流程<br/>RAG 检索 + Prompt 组装"]
    rag --> milvus
    rag --> answerLLM["答案生成<br/>OpenAI Chat Completions<br/>MODEL_AI"]
    answerLLM --> messageDB["对话持久化<br/>Conversation / Message"]
    messageDB --> frontend
```

## 文件处理流程

```mermaid
flowchart TD
    A["用户选择文件<br/>前端 FormData"] --> B["上传源文件<br/>POST /api/v1/document/upload_files<br/>FastAPI UploadFile + aiofiles"]
    B --> C["保存源文件<br/>upload/source_documents"]
    B --> D["登记源文档<br/>SourceDocument<br/>status=uploaded"]

    D --> E["发起解析<br/>POST /api/v1/document/analyze_files"]
    E --> F["占用解析任务<br/>状态校验 + 权限校验<br/>SQLAlchemy AsyncSession"]
    F --> G{"按文件类型分流"}

    G --> PDF["PDF<br/>PyMuPDF 文本版面解析<br/>扫描件走 MinerU OCR<br/>装饰图过滤"]
    G --> PPT["PPT/PPTX<br/>LibreOffice 转 PDF + MinerU<br/>失败回退 python-pptx<br/>装饰图过滤"]
    G --> WORD["Word<br/>python-docx<br/>文本和图片抽取"]
    G --> HTML["HTML/MHTML<br/>BeautifulSoup<br/>Selenium + Chrome 渲染图片"]
    G --> MD["Markdown/TXT<br/>文本解析<br/>本地/远程图片处理"]
    G --> IMG["图片<br/>多模态大模型识别"]
    G --> TABLE["CSV/Excel<br/>表格解析"]

    PDF --> H["统一解析结果<br/>标题、问题简介、原因、评估、检查、方案、要点、图片"]
    PPT --> H
    WORD --> H
    HTML --> H
    MD --> H
    IMG --> H
    TABLE --> H

    H --> I["AI 结构化整理<br/>OpenAI 兼容接口<br/>MODEL_AI"]
    I --> J{"是否提交审核"}

    J -->|是| K["写入审核表<br/>Document_review / KnowledgeDocumentReview<br/>SourceDocument status=review_pending"]
    K --> L["审核界面<br/>待审核 / 全部记录"]
    L --> M{"审核结果"}
    M -->|驳回| N["记录审核意见<br/>SourceDocument 回到 uploaded 或 parse_failed"]
    M -->|通过| O["写正式文档表<br/>DocumentBreakdown 或 DocumentKnowledge<br/>知识库同步章节表"]

    J -->|否, 管理员直入库| O

    O --> P["生成向量 Chunk<br/>主 chunk + 字段/章节 chunk + 表格 chunk<br/>超长内容按 Milvus 限制拆分"]
    P --> Q["嵌入模型编码<br/>Visualized_BGE<br/>BAAI/bge-m3 + Visualized_m3.pth<br/>PyTorch"]
    Q --> R["写入 Milvus<br/>documents_collection_main_chunk<br/>IP + IVF_FLAT 索引"]
    R --> S["更新状态<br/>document.is_vectorized=1<br/>SourceDocument status=vectorized"]

    Q -. "失败" .-> T["向量化失败处理<br/>审核通过不提交<br/>数据库回滚<br/>返回失败原因"]
```

## 问答回复流程

```mermaid
flowchart TD
    A["用户新建会话<br/>POST /api/v1/chats/{chat_id}/session"] --> B["保存 Conversation<br/>MySQL"]
    B --> C["用户提问<br/>POST /api/v1/chats/{chat_id}/completions<br/>可携带 user_uploaded_images"]
    C --> D["保存用户消息<br/>Message role=user<br/>token_count 统计"]

    C --> IMG{"是否上传图片"}
    IMG -->|是| UIMG["上传/校验问答图片<br/>POST /api/v1/chats/{chat_id}/images<br/>upload/images"]
    UIMG --> VIMG["图片语义增强<br/>视觉大模型 describe_image<br/>拼接到检索 query"]
    IMG -->|否| TXT["纯文本 query"]

    VIMG --> E["向量检索<br/>VectorService.search_similar_documents"]
    TXT --> E
    E --> F["查询向量库<br/>Visualized_BGE 编码 query<br/>Milvus similarity search"]
    F --> G["候选合并与过滤<br/>按 doc_id + library_type 聚合<br/>相似度阈值 + 关键词补召回"]
    G --> H["回查 MySQL<br/>过滤已删除文档<br/>补全文档标题和章节"]

    H --> I["构造 RAG Prompt<br/>get_prompt<br/>知识库取命中 chunk 附近章节<br/>故障库拼接七段字段"]
    I --> J["加入上下文<br/>最近历史消息<br/>命中文档图片<br/>用户上传图片<br/>token 预算控制"]

    J --> K{"stream=true?"}
    K -->|是| L["流式生成<br/>StreamingResponse + SSE<br/>OpenAI Chat Completions"]
    K -->|否| M["普通生成<br/>OpenAI Chat Completions"]

    L --> N["清理回答<br/>脱敏<br/>图片路径转 /upload/...<br/>追加参考图片"]
    M --> N
    N --> O["保存 AI 消息<br/>Message role=assistant<br/>ai_reference_doc_ids"]
    O --> P["返回前端展示<br/>答案 + reference + reference_docs"]
```

## 关键技术点

| 模块 | 当前实现 | 主要技术 |
| --- | --- | --- |
| 前端交互 | 文件上传、解析、审核、问答页面 | HTML、CSS、JavaScript、FormData、SSE |
| 后端 API | 路由与鉴权入口 | FastAPI、Depends、JWT、Pydantic |
| 业务数据库 | 用户、源文件、审核、文档、消息 | MySQL、SQLAlchemy AsyncSession、asyncmy |
| 文件存储 | 源文件、解析图片、问答图片 | 本地 upload 目录、FastAPI 静态资源挂载 |
| PDF 解析 | 普通 PDF 和扫描 PDF | PyMuPDF、MinerU OCR、图片过滤 |
| PPT 解析 | PPT 优先转 PDF 后解析 | LibreOffice/soffice、MinerU、python-pptx |
| HTML 解析 | 文本与图片提取 | BeautifulSoup、Selenium、ChromeDriver |
| AI 结构化 | 把非结构化文件整理成文档字段 | OpenAI 兼容接口、MODEL_AI 多模态模型 |
| 审核流 | 新增、修改、删除审核 | document_reviews、knowledge_document_reviews、事务回滚 |
| 向量化 | 文档拆 chunk 并嵌入 | Visualized_BGE、BAAI/bge-m3、PyTorch |
| 向量库 | 存储与检索文档 chunk | Milvus、pymilvus、IP、IVF_FLAT |
| RAG 回复 | 检索证据并生成回答 | VectorService、Prompt 组装、Chat Completions、流式 SSE |

## 重要业务约束

- 源文件上传只保存文件和 SourceDocument 记录，不直接入库。
- 技术人员解析后默认提交审核，审核通过后才写正式文档库。
- 审核通过时如果向量化失败，业务表和审核状态不提交，文档保持待处理状态，前端展示失败原因。
- 文档表和 Milvus 向量库要求一对一一致，`is_vectorized=1` 表示正式文档已完成向量写入。
- 问答只基于检索到的知识文档构造 prompt，参考图片由后端统一转成 `/upload/...` URL 后展示。
