# 维修辅助系统接口文档（当前代码口径摘要）

本文档按当前后端代码整理，用于接口测试和企业对接。原 PDF 中如果仍出现 `X-API-Key`，以本文档为准。

## 基础信息

基础地址：

```text
http://{address}:8000
```

企业接口主要使用 `/api/v1` 前缀。对话接口固定挂载在 `/api/v1/chats`。

## 鉴权方式

当前系统使用用户自己的 `api_key`，通过 `Authorization` 请求头传递：

```http
Authorization: Bearer <用户的api_key>
```

不要使用：

```http
X-API-Key: <用户的api_key>
```

说明：

- `api_key` 是按用户生成的，一个用户一个 key。
- 权限跟 `api_key` 绑定的用户角色一致。
- 测试不同角色时，应分别使用管理员、技术人员、审核人员等不同用户的 `api_key`。
- 当前代码中 `api_key` 前缀通常为 `mas_`。

## 请求头

JSON 请求：

```http
Content-Type: application/json
Authorization: Bearer <用户的api_key>
```

文件上传请求：

```http
Content-Type: multipart/form-data
Authorization: Bearer <用户的api_key>
```

## 返回格式

当前系统存在两套历史成功响应格式。

普通业务接口成功：

```json
{
  "code": 1,
  "msg": "success",
  "data": {}
}
```

聊天主接口成功：

```json
{
  "code": 0,
  "message": null,
  "data": {}
}
```

注意：

- 普通业务接口包括 `/api/v1/document/*`、`/api/v1/review/*`、`/api/v1/user/*`、`/api/v1/source-documents/*`、`/api/v1/tag/*` 等，成功一般为 `code=1`。
- `/api/v1/chats/{chat_id}/session`、`/api/v1/chats/{chat_id}/sessions`、`/api/v1/chats/{chat_id}/completions` 非流式成功一般为 `code=0`。
- `/api/v1/chats/{chat_id}/images` 虽然在 chats 下，但复用旧上传逻辑，成功为 `code=1`。
- 流式问答结束标记为 `{"code":1,"data":"true"}`，这是 SSE 结束标记，不是聊天主响应成功码。

错误响应：

```json
{
  "code": 40000,
  "msg": "错误信息",
  "message": "错误信息",
  "detail": {},
  "trace_id": "xxxx",
  "data": null
}
```

常见错误码：

| code | 含义 |
| --- | --- |
| 40000 | 请求参数错误 |
| 40100 | 未授权 / 登录失效 |
| 40300 | 无权限 |
| 40400 | 资源不存在 |
| 50000 | 服务器内部错误 |
| 40010 | 文件类型不支持 |
| 40011 | 请求核心参数无效 |
| 40012 | 文件解析失败 |
| 40013 | 文件内容过长 |
| 40410 | 文档资源不存在 |
| 40430 | 对话不存在 |
| 40330 | 无权访问该对话 |
| 40040 | 消息上下文过长 |
| 50210 | AI 服务不可用 |

## 对话与 AI 问答

路径前缀：

```text
/api/v1/chats/{chat_id}
```

`chat_id` 当前主要用于路径规范，测试时可传 `test`。

### 创建对话

```http
POST /api/v1/chats/{chat_id}/session
```

请求体：

```json
{
  "name": "新对话"
}
```

cURL：

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/chats/test/session \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "name": "新对话"
  }'
```

成功响应：`code=0`。

### 更新对话名称

```http
PUT /api/v1/chats/{chat_id}/session/{session_id}
```

请求体：

```json
{
  "name": "updated session name"
}
```

cURL：

```bash
curl --request PUT \
  --url http://{address}:8000/api/v1/chats/test/session/1 \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "name": "updated session name"
  }'
```

成功响应：

```json
{
  "code": 0,
  "message": null,
  "data": null
}
```

### 获取对话列表

```http
GET /api/v1/chats/{chat_id}/sessions?page=1&page_size=30&order_by=update_time&desc=true
```

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| page | integer | 否 | 页码，默认 1 |
| page_size | integer | 否 | 每页数量，默认 30 |
| order_by | string | 否 | `create_time` 或 `update_time` |
| desc | boolean | 否 | 是否倒序 |
| name | string | 否 | 按对话名称搜索 |
| id | integer | 否 | 传入时返回指定会话及消息 |

成功响应：`code=0`。

### 删除对话

```http
DELETE /api/v1/chats/{chat_id}/sessions
```

请求体：

```json
{
  "ids": [1]
}
```

成功响应：`code=0`。

### 上传问答图片

```http
POST /api/v1/chats/{chat_id}/images
```

请求类型：`multipart/form-data`

表单字段：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| images | file[] | 是 | 待上传图片 |

cURL：

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/chats/test/images \
  --header 'Authorization: Bearer <用户的api_key>' \
  --form 'images=@/path/to/image.png'
```

成功响应：`code=1`。

返回的 `data[].url` 可作为问答接口的 `user_uploaded_images`。

### AI 问答

```http
POST /api/v1/chats/{chat_id}/completions
```

请求体：

```json
{
  "question": "洗衣机脱水震动大怎么办？",
  "session_id": 1,
  "stream": false,
  "user_uploaded_images": null
}
```

参数说明：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| question | string | 是 | 用户问题 |
| session_id | integer | 是 | 对话 ID |
| stream | boolean | 否 | 是否流式返回 |
| user_uploaded_images | string | 否 | 上传图片后得到的 URL，多个用英文逗号分隔 |

cURL：

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/chats/test/completions \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "question": "洗衣机脱水震动大怎么办？",
    "session_id": 1,
    "stream": false,
    "user_uploaded_images": null
  }'
```

非流式成功响应：`code=0`。

流式响应为 `text/event-stream`，内容事件一般为：

```json
{
  "code": 0,
  "data": {
    "answer": "部分回答内容",
    "final": false
  }
}
```

流结束事件：

```json
{
  "code": 1,
  "data": "true"
}
```

## 文档知识库

路径前缀：

```text
/api/v1/document
```

文档库类型：

| 值 | 说明 |
| --- | --- |
| breakdown | 故障库 |
| knowledge | 知识库 |
| all | 查询接口中同时查询故障库和知识库 |

### 新增文档

```http
POST /api/v1/document/add
```

请求体：

```json
{
  "library_type": "breakdown",
  "tag": ["洗衣机"],
  "title": "洗衣机脱水震动大",
  "problem_intro": "脱水阶段机身震动明显",
  "image_urls": null,
  "causes": "地面不平、负载不均、减震件损坏",
  "evaluation": "检查摆放、负载和减震器",
  "inspection": "检查底脚、桶体、减震杆",
  "solutions": "调平机器，重新分布衣物，更换损坏部件",
  "key_points": "先排查安装和负载，再检查硬件"
}
```

说明：

- 字段名是 `image_urls`，不是 `img_url`。
- `image_urls` 可不传；不传等价于 `null`。
- 有图片时先调用 `/api/v1/document/upload_images`，再把返回的 `url` 放入 `image_urls`。
- `image_urls` 当前后端按字符串处理，多个 URL 推荐用英文逗号加空格分隔，例如 `"upload/images/a.png, upload/images/b.png"`。

cURL：

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/document/add \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "library_type": "breakdown",
    "tag": ["洗衣机"],
    "title": "洗衣机脱水震动大",
    "problem_intro": "脱水阶段机身震动明显",
    "image_urls": null,
    "causes": "地面不平、负载不均、减震件损坏",
    "evaluation": "检查摆放、负载和减震器",
    "inspection": "检查底脚、桶体、减震杆",
    "solutions": "调平机器，重新分布衣物，更换损坏部件",
    "key_points": "先排查安装和负载，再检查硬件"
  }'
```

成功响应：`code=1`。

### 上传文档图片

```http
POST /api/v1/document/upload_images
```

请求类型：`multipart/form-data`

表单字段：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| images | file[] | 是 | 待上传图片 |

cURL：

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/document/upload_images \
  --header 'Authorization: Bearer <用户的api_key>' \
  --form 'images=@/path/to/image.png'
```

成功响应：`code=1`。

### 分页查询文档

```http
POST /api/v1/document/page
```

请求体：

```json
{
  "page": 1,
  "size": 10,
  "library_type": "breakdown",
  "tag": ["洗衣机"]
}
```

成功响应：`code=1`。

### 搜索文档

```http
POST /api/v1/document/query
```

请求体：

```json
{
  "data": "脱水震动",
  "page": 1,
  "size": 10,
  "library_type": "all"
}
```

成功响应：`code=1`。

### 获取文档详情

```http
GET /api/v1/document/get_by_id/{id}?library_type=breakdown
```

成功响应：`code=1`。

### 修改文档

```http
PUT /api/v1/document/update?id={id}&library_type={library_type}
```

请求体字段与新增文档基本一致，成功响应：`code=1`。

### 删除文档

```http
DELETE /api/v1/document/dele/{id}?library_type=breakdown
```

成功响应：`code=1`。

### 批量删除文档

```http
POST /api/v1/document/deletes
```

请求体：

```json
{
  "documents": [
    {
      "id": 1,
      "library_type": "breakdown"
    }
  ]
}
```

成功响应：`code=1`。

## 文档批量导入

路径前缀：

```text
/api/v1/datasets
```

### 上传源文档文件

```http
POST /api/v1/datasets/{dataset_id}/documents
```

`dataset_id` 支持 `breakdown` 或 `knowledge`。

cURL：

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/datasets/knowledge/documents \
  --header 'Authorization: Bearer <用户的api_key>' \
  --form 'files=@/path/to/example.pdf'
```

成功响应：`code=1`，字段为 `message`。

### 解析源文档文件

```http
POST /api/v1/datasets/analyze
```

请求体：

```json
{
  "file_list": ["upload/source_documents/example.pdf"],
  "file_name": ["example.pdf"],
  "submit_for_review": true,
  "library_type": "knowledge",
  "tag": ["售后手册"]
}
```

成功响应：`code=1`，字段为 `message`。

### 删除批量导入文档

```http
DELETE /api/v1/datasets/{dataset_id}/documents
```

请求体：

```json
{
  "ids": [1]
}
```

成功响应：`code=1`，字段为 `message`。

## 源文档管理

路径前缀：

```text
/api/v1/source-documents
```

### 分页查询源文档

```http
GET /api/v1/source-documents/page?page=1&size=12
```

常用查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| page | integer | 否 | 页码 |
| size | integer | 否 | 每页数量，默认 12，最大 50 |
| keyword | string | 否 | 文件名关键词 |
| category | string | 否 | 文件分类 |
| source_status | string | 否 | 源文档状态 |
| pending_only | boolean | 否 | 是否只看待处理源文档 |

成功响应：`code=1`。

### 删除源文档

```http
DELETE /api/v1/source-documents/{source_id}
```

成功响应：`code=1`。

## 审核流程

路径前缀：

```text
/api/v1/review
```

### 提交审核

```http
POST /api/v1/review/create
```

请求体：

```json
{
  "action_type": 1,
  "document_library_type": "breakdown",
  "tag": ["洗衣机"],
  "title": "洗衣机脱水震动大",
  "problem_intro": "脱水阶段机身震动明显"
}
```

说明：

- `action_type=1` 新增。
- `action_type=2` 修改，通常需要 `document_id`。
- `action_type=3` 删除，通常需要 `document_id`。

成功响应：`code=1`。

### 获取待审核列表

```http
GET /api/v1/review/pending
```

成功响应：`code=1`。

### 获取全部审核列表

```http
GET /api/v1/review/all
```

成功响应：`code=1`。

### 获取个人审核提交

```http
GET /api/v1/review/get_by_id
```

成功响应：`code=1`。

### 通过审核

```http
POST /api/v1/review/approve/{review_id}
```

请求体：

```json
{
  "review_comment": "审核通过"
}
```

成功响应：`code=1`。

### 驳回审核

```http
POST /api/v1/review/reject/{review_id}
```

请求体：

```json
{
  "review_comment": "请补充排查步骤"
}
```

成功响应：`code=1`。

### 撤回审核

```http
POST /api/v1/review/withdraw/{review_id}
```

成功响应：`code=1`。

## 标签管理

路径前缀：

```text
/api/v1/tag
```

| 方法 | 路径 | 说明 | 成功码 |
| --- | --- | --- | --- |
| GET | `/list` | 获取所有标签 | 1 |
| POST | `/page` | 分页查询标签 | 1 |
| POST | `/add` | 新增标签 | 1 |
| PATCH | `/update` | 更新标签 | 1 |
| DELETE | `/delete/{tag_id}` | 删除标签 | 1 |

## 用户中心

路径前缀：

```text
/api/v1/user
```

用户中心接口一般给内部前端使用。企业调用时也可以使用 `api_key`，返回的是该 `api_key` 绑定用户的信息。

| 方法 | 路径 | 说明 | 成功码 |
| --- | --- | --- | --- |
| GET | `/profile` | 获取当前用户资料 | 1 |
| PATCH | `/update` | 更新当前用户信息 | 1 |
| PUT | `/change_password` | 修改密码 | 1 |

## 认证接口

路径前缀：

```text
/api/v1/auth
```

这些接口主要服务内部前端登录流程。

| 方法 | 路径 | 说明 | 备注 |
| --- | --- | --- | --- |
| POST | `/register` | 用户注册 | 注册后等待管理员审核 |
| POST | `/login` | 用户登录 | 返回 JWT token 和用户信息 |
| POST | `/refresh` | 刷新 JWT token | 使用 refresh token |

企业系统对接优先使用用户 `api_key`，不需要先调用登录接口。

## 管理员接口

路径前缀：

```text
/api/v1/admin
```

管理员接口主要供后台管理页面使用，包括：

- 数据看板：`GET /dashboard`
- 重建搜索索引：`POST /search_index/rebuild`
- 敏感词管理：`GET/POST/PATCH/DELETE /sensitive_terms`
- 角色组管理：`GET/POST/PATCH /role_groups`
- 注册申请审核：`GET /registrations`、`POST /registrations/approve`、`POST /registrations/reject`
- 用户管理：`POST /add_user`、`PATCH /update_user`、`GET /users`、`GET /user/{id}`、`POST /users/page`、`POST /query`

成功响应一般为 `code=1`。

## 接口测试建议

1. 所有需要鉴权的接口都使用 `Authorization: Bearer <用户的api_key>`。
2. 不要在新文档或测试脚本中继续使用 `X-API-Key`。
3. 对话主接口成功判断 `code===0`。
4. 普通业务接口成功判断 `code===1`。
5. 文件上传不要手动设置 JSON 请求体，使用 `multipart/form-data`。
6. 测权限时不要共用一个管理员 key，应按角色分别准备用户和 `api_key`。
