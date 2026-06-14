# 企业接口文档

基础地址示例：`http://{address}:8000`
企业统一前缀：`/api/v1`

## 鉴权方式

企业调用业务接口统一使用用户创建时保存的 api_key，通过 Authorization 头传递：

```http
Authorization: Bearer <用户的api_key>
```

JSON 请求默认请求头：

```http
Content-Type: application/json
```

文件上传接口使用：

```http
Content-Type: multipart/form-data
```

通用成功响应：

```json
{
  "code": 1,
  "msg": "success",
  "data": {}
}
```

`/api/v1/chats/*` 部分接口使用：

```json
{
  "code": 0,
  "message": null,
  "data": {}
}
```

## 测试 api_key

本接口文档中的示例 api_key 来自用户创建时自动生成的字段，企业测试时可直接放在 Authorization 头中：

```http
Authorization: Bearer mas_qlMaGy3bajobnZd3f3U-z63_WBpJPFChSc7pOvBo0Rc
```

该 api_key 绑定具体用户，接口权限取决于该用户的角色和权限。正式交付时建议按企业实际用户单独创建。

## 对话与 AI 问答

### 创建对话

#### 请求

- 方法：`POST`
- URL：`/api/v1/chats/{chat_id}/session`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "name": "new session"
}
```

#### 请求参数

- `chat_id`：`Path`，`string`，必填，聊天助手 ID，测试可传 `test`。
- `name`：`Body`，`string`，必填，会话名称。
- `user_id`：`Body`，`string`，可选，当前后端实际以用户的 api_key 对应用户为准。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/chats/test/session \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "name": "new session"
  }'
```

#### 响应

```json
{
  "code": 0,
  "message": null,
  "data": {
    "chat_id": "test",
    "id": 1,
    "messages": [],
    "name": "new session",
    "create_time": 1781056800.0,
    "update_time": 1781056800.0
  }
}
```

### 更新对话名称

#### 请求

- 方法：`PUT`
- URL：`/api/v1/chats/{chat_id}/session/{session_id}`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "name": "updated session name"
}
```

#### 请求参数

- `chat_id`：`Path`，`string`，必填。
- `session_id`：`Path`，`integer`，必填，会话 ID。
- `name`：`Body`，`string`，必填。

#### cURL

```bash
curl --request PUT \
  --url http://{address}:8000/api/v1/chats/test/session/1 \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "name": "updated session name"
  }'
```

#### 响应

```json
{
  "code": 0,
  "message": null,
  "data": null
}
```

### 获取对话列表

#### 请求

- 方法：`GET`
- URL：`/api/v1/chats/{chat_id}/sessions`
- 请求头：
  - `Authorization: Bearer <用户的api_key>`
- 正文：无

#### 请求参数

- `chat_id`：`Path`，`string`，必填。
- `page`：`Query`，`integer`，可选，默认 `1`。
- `page_size`：`Query`，`integer`，可选，默认 `30`。
- `order_by`：`Query`，`string`，可选，默认 `create_time`，可选 `create_time`、`update_time`。
- `desc`：`Query`，`boolean`，可选，默认 `true`。
- `name`：`Query`，`string`，可选。
- `id`：`Query`，`integer`，可选。

#### cURL

```bash
curl --request GET \
  --url 'http://{address}:8000/api/v1/chats/test/sessions?page=1&page_size=30' \
  --header 'Authorization: Bearer <用户的api_key>'
```

#### 响应

```json
{
  "code": 0,
  "message": null,
  "data": {
    "total": 1,
    "page": 1,
    "page_size": 30,
    "sessions": [
      {
        "chat": "test",
        "name": "new session",
        "messages": []
      }
    ]
  }
}
```

### 删除对话

#### 请求

- 方法：`DELETE`
- URL：`/api/v1/chats/{chat_id}/sessions`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "ids": [1]
}
```

#### 请求参数

- `chat_id`：`Path`，`string`，必填。
- `ids`：`Body`，`integer[]`，必填，会话 ID 列表。

#### cURL

```bash
curl --request DELETE \
  --url http://{address}:8000/api/v1/chats/test/sessions \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "ids": [1]
  }'
```

#### 响应

```json
{
  "code": 0,
  "message": null,
  "data": null
}
```

### AI 问答

#### 请求

- 方法：`POST`
- URL：`/api/v1/chats/{chat_id}/completions`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "question": "洗衣机脱水时震动很大，可能是什么原因？",
  "session_id": 1,
  "stream": true,
  "user_uploaded_images": null
}
```

#### 请求参数

- `chat_id`：`Path`，`string`，必填。
- `question`：`Body`，`string`，必填，用户问题。
- `session_id`：`Body`，`integer`，必填，会话 ID。
- `stream`：`Body`，`boolean`，必填，是否流式返回。
- `user_uploaded_images`：`Body`，`string`，可选，图片路径，多个路径用 `, ` 分隔。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/chats/test/completions \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "question": "洗衣机脱水时震动很大，可能是什么原因？",
    "session_id": 1,
    "stream": true,
    "user_uploaded_images": null
  }'
```

#### 响应

`stream=true` 时响应类型为 `text/event-stream`：

```text
data: {"id":2,"session_id":1,"answer":"可能原因包括...","code":1}

data: {"code":1,"data":"true"}
```

`stream=false` 时：

```json
{
  "code": 0,
  "message": null,
  "data": {
    "answer": "可能原因包括地面不平、负载不均、减震件损坏等。",
    "reference": "1,2",
    "id": 2,
    "session_id": 1
  }
}
```

## 对话附件与消息

本节接口按对话资源统一收敛到 `/api/v1/chats/{chat_id}` 下。

### 上传问答图片

#### 请求

- 方法：`POST`
- URL：`/api/v1/chats/{chat_id}/images`
- 请求头：
  - `Content-Type: multipart/form-data`
  - `Authorization: Bearer <用户的api_key>`
- 正文：`multipart/form-data`

#### 请求参数

- `chat_id`：`Path`，`string`，必填，聊天助手 ID，测试可传 `test`。
- `images`：`FormData`，`file[]`，必填，图片文件，可多个。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/chats/test/images \
  --header 'Authorization: Bearer <用户的api_key>' \
  --form 'images=@/path/to/image.jpg'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": [
    {
      "url": "upload/images/20260610_xxx.jpg",
      "filename": "20260610_xxx.jpg",
      "original_name": "image.jpg"
    }
  ]
}
```

### 发送问题

发送问题统一使用前文 `AI 问答` 接口，不再单独提供 `/message/ask`。

#### 请求

- 方法：`POST`
- URL：`/api/v1/chats/{chat_id}/completions`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "question": "洗衣机脱水时震动很大，可能是什么原因？",
  "session_id": 1,
  "stream": true,
  "user_uploaded_images": null
}
```

#### 请求参数

- `chat_id`：`Path`，`string`，必填。
- `question`：`Body`，`string`，必填，用户问题。
- `session_id`：`Body`，`integer`，必填。
- `user_uploaded_images`：`Body`，`string`，可选。
- `stream`：`Body`，`boolean`，必填，是否流式返回。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/chats/test/completions \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "question": "洗衣机脱水时震动很大，可能是什么原因？",
    "session_id": 1,
    "stream": true,
    "user_uploaded_images": null
  }'
```

#### 响应

```text
data: {"code":0,"data":{"id":2,"session_id":1,"answer":"可能原因包括...","reference":{"total":3,"doc_aggs":[]}}}

data: {"code":1,"data":"true"}
```

### 获取对话消息

#### 请求

- 方法：`GET`
- URL：`/api/v1/chats/{chat_id}/sessions`
- 请求头：
  - `Authorization: Bearer <用户的api_key>`
- 正文：无

#### 请求参数

- `chat_id`：`Path`，`string`，必填。
- `id`：`Query`，`integer`，必填，会话 ID。传入后返回该会话及其消息列表。

#### cURL

```bash
curl --request GET \
  --url 'http://{address}:8000/api/v1/chats/test/sessions?id=1' \
  --header 'Authorization: Bearer <用户的api_key>'
```

#### 响应

```json
{
  "code": 1,
  "message": null,
  "data": {
    "total": 1,
    "page": 1,
    "page_size": 30,
    "sessions": [
      {
        "chat": "test",
        "id": 1,
        "name": "new session",
        "messages": [
          {
            "id": 1,
            "role": "user",
            "content": "洗衣机脱水时震动很大，可能是什么原因？",
            "created_time": "2026-06-10T10:00:00"
          }
        ]
      }
    ]
  }
}
```

## 文档知识库

`library_type` 说明：

- `breakdown`：故障库，默认值。
- `knowledge`：知识库。
- `all`：查询接口中同时查询故障库和知识库。

### 新增文档

#### 请求

- 方法：`POST`
- URL：`/api/v1/document/add`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

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

#### 请求参数

- `library_type`：`Body`，`string`，可选，默认 `breakdown`。
- `tag`：`Body`，`string[]`，可选，文档标签。
- `title`：`Body`，`string`，必填，文档标题。
- `problem_intro`：`Body`，`string`，可选，问题描述。
- `causes`：`Body`，`string`，可选，原因分析。
- `evaluation`：`Body`，`string`，可选，评估方法。
- `inspection`：`Body`，`string`，可选，检查步骤。
- `solutions`：`Body`，`string`，可选，解决方案。
- `key_points`：`Body`，`string`，可选，关键要点。

#### cURL

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
    "causes": "地面不平、负载不均、减震件损坏",
    "evaluation": "检查摆放、负载和减震器",
    "inspection": "检查底脚、桶体、减震杆",
    "solutions": "调平机器，重新分布衣物，更换损坏部件",
    "key_points": "先排查安装和负载，再检查硬件"
  }'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "id": 1,
    "title": "洗衣机脱水震动大",
    "library_type": "breakdown"
  }
}
```

### 上传文档图片

#### 请求

- 方法：`POST`
- URL：`/api/v1/document/upload_images`
- 请求头：
  - `Content-Type: multipart/form-data`
  - `Authorization: Bearer <用户的api_key>`
- 正文：`multipart/form-data`

#### 请求参数

- `images`：`FormData`，`file[]`，必填，图片文件，可多个。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/document/upload_images \
  --header 'Authorization: Bearer <用户的api_key>' \
  --form 'images=@/path/to/image.jpg'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": [
    {
      "url": "upload/images/20260610_xxx.jpg",
      "filename": "20260610_xxx.jpg"
    }
  ]
}
```

### 分页查询文档

#### 请求

- 方法：`POST`
- URL：`/api/v1/document/page`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "page": 1,
  "size": 10,
  "library_type": "all",
  "tag": ["洗衣机"]
}
```

#### 请求参数

- `page`：`Body`，`integer`，可选，默认 `1`。
- `size`：`Body`，`integer`，可选，默认 `10`。
- `library_type`：`Body`，`string`，可选，支持 `breakdown`、`knowledge`、`all`。
- `tag`：`Body`，`string[]`，可选，标签过滤。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/document/page \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "page": 1,
    "size": 10,
    "library_type": "all",
    "tag": ["洗衣机"]
  }'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "total": 1,
    "page": 1,
    "page_size": 10,
    "documents": [
      {
        "id": 1,
        "library_type": "breakdown",
        "title": "洗衣机脱水震动大"
      }
    ]
  }
}
```

### 搜索文档

#### 请求

- 方法：`POST`
- URL：`/api/v1/document/query`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "data": "脱水震动",
  "library_type": "all",
  "page": 1,
  "size": 10
}
```

#### 请求参数

- `data`：`Body`，`string`，必填，搜索关键词。
- `library_type`：`Body`，`string`，可选，支持 `breakdown`、`knowledge`、`all`。
- `page`：`Body`，`integer`，可选，默认 `1`。
- `size`：`Body`，`integer`，可选，默认 `10`。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/document/query \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "data": "脱水震动",
    "library_type": "all",
    "page": 1,
    "size": 10
  }'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "total": 1,
    "page": 1,
    "page_size": 10,
    "documents": [
      {
        "id": 1,
        "library_type": "breakdown",
        "title": "洗衣机脱水震动大"
      }
    ]
  }
}
```

### 获取文档详情

#### 请求

- 方法：`GET`
- URL：`/api/v1/document/get_by_id/{id}`
- 请求头：
  - `Authorization: Bearer <用户的api_key>`
- 正文：无

#### 请求参数

- `id`：`Path`，`integer`，必填，文档 ID。
- `library_type`：`Query`，`string`，可选，默认 `breakdown`。

#### cURL

```bash
curl --request GET \
  --url 'http://{address}:8000/api/v1/document/get_by_id/1?library_type=breakdown' \
  --header 'Authorization: Bearer <用户的api_key>'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "id": 1,
    "library_type": "breakdown",
    "title": "洗衣机脱水震动大"
  }
}
```

### 修改文档

#### 请求

- 方法：`PUT`
- URL：`/api/v1/document/update?id={id}&library_type={library_type}`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "library_type": "knowledge",
  "tag": ["售后手册"],
  "title": "洗衣机保养知识",
  "problem_intro": "洗衣机日常保养说明",
  "causes": "",
  "evaluation": "",
  "inspection": "定期检查进水管、排水管和过滤网",
  "solutions": "清理过滤网，保持桶内干燥",
  "key_points": "按周期维护，减少故障发生"
}
```

#### 请求参数

- `id`：`Query`，`integer`，必填，文档 ID。
- `library_type`：`Query`，`string`，可选，默认 `breakdown`；传 `knowledge` 时修改知识库文档。
- `tag`：`Body`，`string[]`，可选，文档标签。
- `title`：`Body`，`string`，必填，文档标题。
- `problem_intro`：`Body`，`string`，可选，问题描述或知识说明。
- `causes`：`Body`，`string`，可选，原因分析。
- `evaluation`：`Body`，`string`，可选，评估方法。
- `inspection`：`Body`，`string`，可选，检查步骤。
- `solutions`：`Body`，`string`，可选，解决方案。
- `key_points`：`Body`，`string`，可选，关键要点。

#### cURL

```bash
curl --request PUT \
  --url 'http://{address}:8000/api/v1/document/update?id=1&library_type=knowledge' \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "library_type": "knowledge",
    "tag": ["售后手册"],
    "title": "洗衣机保养知识",
    "problem_intro": "洗衣机日常保养说明",
    "inspection": "定期检查进水管、排水管和过滤网",
    "solutions": "清理过滤网，保持桶内干燥",
    "key_points": "按周期维护，减少故障发生"
  }'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": null
}
```

### 删除文档

#### 请求

- 方法：`DELETE`
- URL：`/api/v1/document/dele/{id}`
- 请求头：
  - `Authorization: Bearer <用户的api_key>`
- 正文：无

#### 请求参数

- `id`：`Path`，`integer`，必填，文档 ID。
- `library_type`：`Query`，`string`，可选，默认 `breakdown`；传 `knowledge` 时删除知识库文档。

#### cURL

```bash
curl --request DELETE \
  --url 'http://{address}:8000/api/v1/document/dele/1?library_type=knowledge' \
  --header 'Authorization: Bearer <用户的api_key>'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": null
}
```

## 文档批量导入

这些接口用于文档或源文档的批量上传和解析。

### 上传源文档文件

#### 请求

- 方法：`POST`
- URL：`/api/v1/datasets/{dataset_id}/documents`
- 请求头：
  - `Content-Type: multipart/form-data`
  - `Authorization: Bearer <用户的api_key>`
- 正文：`multipart/form-data`

#### 请求参数

- `dataset_id`：`Path`，`string`，必填，文档库，支持 `breakdown` 或 `knowledge`。
- `files`：`FormData`，`file[]`，必填，待上传文件。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/datasets/knowledge/documents \
  --header 'Authorization: Bearer <用户的api_key>' \
  --form 'files=@/path/to/example.pdf'
```

#### 响应

```json
{
  "code": 1,
  "message": null,
  "data": {
    "success_origin_filename": ["example.pdf"],
    "success_file_url": ["upload/source_documents/example.pdf"],
    "error_origin_filename": []
  }
}
```

### 解析源文档文件

#### 请求

- 方法：`POST`
- URL：`/api/v1/datasets/analyze`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "file_list": ["upload/source_documents/example.pdf"],
  "file_name": ["example.pdf"],
  "submit_for_review": true,
  "library_type": "knowledge",
  "tag": ["售后手册"]
}
```

#### 请求参数

- `file_list`：`Body`，`string[]`，必填，上传后返回的文件路径。
- `file_name`：`Body`，`string[]`，必填，原始文件名。
- `submit_for_review`：`Body`，`boolean`，可选，是否提交审核。
- `library_type`：`Body`，`string`，必填，文档库。
- `tag`：`Body`，`string[]`，可选，文档标签。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/datasets/analyze \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "file_list": ["upload/source_documents/example.pdf"],
    "file_name": ["example.pdf"],
    "submit_for_review": true,
    "library_type": "knowledge",
    "tag": ["售后手册"]
  }'
```

#### 响应

```json
{
  "code": 1,
  "message": null,
  "data": {
    "success_origin_filename": ["example.pdf"],
    "success_file_url": ["upload/source_documents/example.pdf"],
    "error_origin_filename": []
  }
}
```

### 删除批量导入文档

#### 请求

- 方法：`DELETE`
- URL：`/api/v1/datasets/{dataset_id}/documents`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "ids": [1]
}
```

#### 请求参数

- `dataset_id`：`Path`，`string`，必填，文档库，支持 `breakdown` 或 `knowledge`。
- `ids`：`Body`，`integer[]`，必填，文档 ID 列表。

#### cURL

```bash
curl --request DELETE \
  --url http://{address}:8000/api/v1/datasets/knowledge/documents \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "ids": [1]
  }'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": null
}
```

## 源文档管理

### 分页查询源文档

#### 请求

- 方法：`GET`
- URL：`/api/v1/source-documents/page`
- 请求头：
  - `Authorization: Bearer <用户的api_key>`
- 正文：无

#### 请求参数

- `page`：`Query`，`integer`，可选，默认 `1`。
- `size`：`Query`，`integer`，可选，默认 `12`，最大 `50`。
- `keyword`：`Query`，`string`，可选。
- `category`：`Query`，`string`，可选。
- `source_status`：`Query`，`string`，可选。
- `pending_only`：`Query`，`boolean`，可选，默认 `false`。

#### cURL

```bash
curl --request GET \
  --url 'http://{address}:8000/api/v1/source-documents/page?page=1&size=12' \
  --header 'Authorization: Bearer <用户的api_key>'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "total": 1,
    "page": 1,
    "page_size": 12,
    "source_documents": [
      {
        "id": 1,
        "origin_file_name": "example.pdf",
        "stored_file_path": "upload/source_documents/example.pdf",
        "status": "uploaded"
      }
    ]
  }
}
```

## 审核流程

审核状态：`0` 待审核，`1` 已通过，`2` 已驳回，`3` 已撤回。

操作类型：`1` 新增，`2` 修改，`3` 删除。

### 提交审核

#### 请求

- 方法：`POST`
- URL：`/api/v1/review/create`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "action_type": 1,
  "document_library_type": "breakdown",
  "tag": ["洗衣机"],
  "title": "洗衣机脱水震动大",
  "problem_intro": "脱水阶段机身震动明显"
}
```

#### 请求参数

- `action_type`：`Body`，`integer`，必填，操作类型。
- `document_library_type`：`Body`，`string`，必填，文档库。
- `document_id`：`Body`，`integer`，可选，修改或删除时传入。
- `tag`：`Body`，`string[]`，可选，文档标签。
- `title`：`Body`，`string`，可选，文档标题。
- `problem_intro`：`Body`，`string`，可选，问题描述。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/review/create \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "action_type": 1,
    "document_library_type": "breakdown",
    "tag": ["洗衣机"],
    "title": "洗衣机脱水震动大",
    "problem_intro": "脱水阶段机身震动明显"
  }'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "id": 1,
    "title": "洗衣机脱水震动大",
    "status": 0,
    "action_type": 1
  }
}
```

### 获取待审核列表

#### 请求

- 方法：`GET`
- URL：`/api/v1/review/pending`
- 请求头：
  - `Authorization: Bearer <用户的api_key>`
- 正文：无

#### 请求参数

无。

#### cURL

```bash
curl --request GET \
  --url http://{address}:8000/api/v1/review/pending \
  --header 'Authorization: Bearer <用户的api_key>'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": []
}
```

### 通过审核

#### 请求

- 方法：`POST`
- URL：`/api/v1/review/approve/{review_id}`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "review_comment": "审核通过"
}
```

#### 请求参数

- `review_id`：`Path`，`integer`，必填，审核记录 ID。
- `review_comment`：`Body`，`string`，可选，审核意见。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/review/approve/1 \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "review_comment": "审核通过"
  }'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "id": 1,
    "status": 1
  }
}
```

### 驳回审核

#### 请求

- 方法：`POST`
- URL：`/api/v1/review/reject/{review_id}`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "review_comment": "请补充排查步骤"
}
```

#### 请求参数

- `review_id`：`Path`，`integer`，必填，审核记录 ID。
- `review_comment`：`Body`，`string`，可选，审核意见。

#### cURL

```bash
curl --request POST \
  --url http://{address}:8000/api/v1/review/reject/1 \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "review_comment": "请补充排查步骤"
  }'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "id": 1,
    "status": 2
  }
}
```

## 用户中心

用户中心接口一般给内部前端使用。企业文档中如需调用，也可以使用 api_key，但返回的是 api_key 绑定用户的信息。

### 获取当前用户资料

#### 请求

- 方法：`GET`
- URL：`/api/v1/user/profile`
- 请求头：
  - `Authorization: Bearer <用户的api_key>`
- 正文：无

#### 请求参数

无。

#### cURL

```bash
curl --request GET \
  --url http://{address}:8000/api/v1/user/profile \
  --header 'Authorization: Bearer <用户的api_key>'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "id": 1,
    "username": "admin",
    "full_name": "系统管理员",
    "role": "admin"
  }
}
```

### 更新当前用户信息

#### 请求

- 方法：`PATCH`
- URL：`/api/v1/user/update`
- 请求头：
  - `Content-Type: application/json`
  - `Authorization: Bearer <用户的api_key>`
- 正文：

```json
{
  "full_name": "系统管理员",
  "department": "售后服务部"
}
```

#### 请求参数

- `full_name`：`Body`，`string`，可选，姓名。
- `phone`：`Body`，`string`，可选，手机号。
- `email`：`Body`，`string`，可选，邮箱。
- `department`：`Body`，`string`，可选，部门。

#### cURL

```bash
curl --request PATCH \
  --url http://{address}:8000/api/v1/user/update \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <用户的api_key>' \
  --data '{
    "full_name": "系统管理员",
    "department": "售后服务部"
  }'
```

#### 响应

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "id": 1,
    "full_name": "系统管理员",
    "department": "售后服务部"
  }
}
```

