# Codex 本地运行记录

## 分支与提交

- 当前工作分支：`codex`
- 分支来源：`origin/main`
- 本次操作未提交、未推送。

## 已完成配置

- 已启动 Docker Desktop。
- 已启动已有 Milvus 容器：`milvus-etcd`、`milvus-minio`、`milvus-standalone`。
- Milvus 服务端口：`localhost:19530`。
- 后端已启动成功：`http://localhost:8000`。
- 前端已启动成功：`http://localhost/index.html`。
- 管理员登录已验证成功：`admin` / `123456` / `admin`。
- 已创建运行目录：`upload/images`、`upload/ask`、`upload/documents`、`bge`、`bge/model`、`embedding-model`。
- 已将本机已有权重 `E:\yanjuProject\bge\Visualized_m3.pth` 复制到 `E:\设备维修辅助系统\app\bge\Visualized_m3.pth`。
- 已创建目录联接：`E:\设备维修辅助系统\app\bge\bge-m3` -> `E:\yanjuProject\bge\model`。
- 已更新 `.env` 中的本地路径，使上传目录、文档目录、模型权重路径指向当前项目目录。

## 已解决问题

- MySQL 用户名：`tomlzk`
- MySQL 密码：`lvzhikang2004`
- MySQL 数据库：`maintenance_system`
- MySQL 端口：`localhost:3306`
- SQLAlchemy 连接串：`mysql+asyncmy://tomlzk:lvzhikang2004@localhost:3306/maintenance_system`
- 后端模型加载曾因内存不足失败，已通过释放内存并为 `torch.load` 增加内存映射参数解决。

## 后续启动命令

```powershell
cd E:\设备维修辅助系统\app
docker start milvus-etcd milvus-minio milvus-standalone
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 2026-06-03 多知识库管理修改说明

- 已创建并关联远端分支：`codex` -> `origin/codex`；本次没有提交代码，也没有推送代码改动。
- 新增故障库表模型：`document_breakdown`。
- 新增知识库表模型：`document_knowledge`。
- `Document` 默认指向 `DocumentBreakdown`，原因是旧接口不传库类型时仍按故障库工作，降低对现有前端的破坏。
- 两张文档表都新增 `tag` JSON 字段，用来保存类似 `["机械", "电气"]` 的标签数组。
- 文档接口新增 `library_type` 字段，取值为 `breakdown` 或 `knowledge`；默认值是 `breakdown`。
- 文档接口新增 `tag` 字段，后端会清理空字符串并按 JSON 数组存储。
- `/document/add` 会按 `library_type` 写入对应表，避免故障文档和知识文档混在同一张表。
- `/document/page` 和 `/document/query` 会按 `library_type` 查询对应表，原因是分表后只扫目标库可以减少无关数据查询。
- `/document/page` 和 `/document/query` 支持 `tag` 过滤，原因是设备可能同时涉及机械、电气等多个语义标签。
- 文件解析导入接口也支持 `library_type` 和 `tag`，原因是批量导入不能绕回旧的默认文档表。
- 审核表 `document_reviews` 新增 `document_library_type` 和 `tag`，原因是审核通过时必须知道写回故障库还是知识库。
- 启动迁移会创建新表、补审核表字段，并在 `document_breakdown` 为空时把旧 `documents` 数据迁入故障库表。
- 向量库内部给知识库文档 id 加 `1000000000` 偏移，原因是两张 MySQL 表都会从 `id=1` 开始，自增 id 在 Milvus 里会冲突。
- 向量 metadata 保存真实 `source_doc_id` 和 `library_type`，原因是搜索返回时要回查正确的 MySQL 表。
- 问答提示词回查已按 `library_type` 分别读取 `document_breakdown` 和 `document_knowledge`。
- 添加文档页面已增加“文档库”和“标签”字段，原因是手动新增文档时需要选择故障库或知识库并录入设备语义标签。
- 批量导入页面已增加“文档库”和“标签”字段，原因是文件解析后也需要写入正确的目标库。
- `static/js/api.js` 的 `analyzeFiles` 已透传 `library_type` 和 `tag`，原因是导入页面的选择必须随解析请求一起发送到后端。

### 新字段示例

```json
{
  "library_type": "knowledge",
  "tag": ["机械", "电气"],
  "title": "某设备维护知识",
  "problem_intro": "..."
}
```

```powershell
cd E:\设备维修辅助系统\app\static
python -m http.server 80
```

## 登录初始化

项目 README 建议在首次运行后插入管理员账号，密码为 `123456` 的 MD5 值：

```sql
INSERT INTO users (username, password, phone, email, full_name, status, role, department, created_time, last_login)
VALUES ('admin', 'e10adc3949ba59abbe56e057f20f883e', '17812355311', 'admin@whut.edu.com', '管理员', 1, 0, '管理部', NOW(), NOW());
```
