# 配置指南 - 新下载版本运行前必须修改

## 1. 数据库配置 (database.py)

**文件位置**: `database.py`

需要修改数据库连接字符串，根据你的本地MySQL配置：

```python
SQLALCHEMY_DATABASE_URL = "mysql+asyncmy://root:123456@localhost:3306/maintenance_system"
```

修改说明：
- `root` - 如果数据库用户不是root，改成你的用户名
- `123456` - 改成你的MySQL密码
- `localhost` - 如果用远程数据库，改成服务器IP
- `3306` - 如果MySQL端口不是3306，改成你的端口
- `maintenance_system` - 改成你的数据库名

**数据库初始化**：
1. 在MySQL中创建数据库：`CREATE DATABASE maintenance_system;`
2. 插入初始管理员用户（密码为123456）：
```sql
INSERT INTO users (username, password, phone, email, full_name, status, role, department, created_time, last_login)
VALUES ('admin', 'e10adc3949ba59abbe56e057f20f883e', '17812355311', 'admin@whut.edu.com', '管理员', 1, 0, '管理部', NOW(), NOW());
```

---

## 2. 模型路径配置

### 方案A：修改硬编码路径（快速）

需要修改这些文件中的路径：

#### main.py (第42行)
```python
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "你的项目路径\\bge\\model")
```

#### utils/VectorStoreMultimodal.py (第7行和后续)
```python
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "你的项目路径\\bge\\model")

# 大约第43行
self.model_weight = os.getenv("MODEL_WEIGHT", "你的项目路径\\bge\\Visualized_m3.pth")

# 大约第50-54行
IMAGE_DIR: str = os.getenv("IMAGE_DIR", "你的项目路径\\upload\\images")
BASE_DIR: str = os.getenv("BASE_DIR", "你的项目路径")
MESSAGE_IMAGE_DIR: str = os.getenv("MESSAGE_IMAGE_DIR", "你的项目路径\\upload\\images")
MESSAGE_BASE_DIR: str = os.getenv("MESSAGE_BASE_DIR", "你的项目路径")
```

#### utils/VectorService.py (第23-24行)
```python
os.environ["HF_HOME"] = os.getenv("MODEL_DOWNLOAD_URL", "你的项目路径\\bge\\model")
```

### 方案B：使用.env文件（推荐）

在项目根目录创建 `.env` 文件：

```ini
# 模型和路径配置
MODEL_DOWNLOAD_URL=你的项目路径\bge\model
MODEL_WEIGHT=你的项目路径\bge\Visualized_m3.pth
MODEL_NAME=BAAI/bge-m3

# 文件上传路径
IMAGE_DIR=upload/images
BASE_DIR=你的项目路径
MESSAGE_IMAGE_DIR=upload/images
MESSAGE_BASE_DIR=你的项目路径

# 向量数据库配置
TOP_K=3
CHUNK_SIZE=500
OVERLAP=50

# AI服务器配置（学校校园网）
SERVER_IP=192.168.246.200
API_KEY=EMPTY
MODEL_AI=/models/Qwen3-VL-8B-Instruct
```

---

## 3. AI服务器配置 (utils/VectorStoreMultimodal.py)

**第61-63行** - 这些是学校内网的AI服务器配置，需要确保你在校园网或VPN环境下：

```python
self.ai = os.getenv("SERVER_IP", "192.168.246.200")  # 学校AI服务器IP
self.api_key = os.getenv("API_KEY", "EMPTY")
self.model_chat = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
```

**重要**: 确保你在以下环境之一运行：
- 学校校园网内
- 连接学校VPN

---

## 4. Milvus向量数据库配置

### 快速开始：
1. 确保已安装Docker
2. 在项目根目录运行：
```bash
docker-compose up -d
```

这会自动启动Milvus服务（默认在19530端口）

### 配置文件：
`docker-compose.yml` - 通常无需修改，除非端口冲突

---

## 5. 项目目录准备

确保以下目录存在（如没有则创建）：

```
创建目录：
- upload/
  - images/
  - documents/
  - ask/
- bge/
  - model/  (模型权重会自动下载到此)
- volumes/  (Milvus自动创建)
```

---

## 6. 依赖安装

```bash
# 根据你的硬件选择对应版本的torch
# CPU版本：
pip install -r requirements.txt

# 如果有NVIDIA显卡，建议安装GPU版本的torch（requirements.txt中可能需要调整）
```

**注意**: 
- 第一次运行会自动下载BGE-M3模型（约1.5GB）
- 确保网络连接良好
- 模型会下载到 `MODEL_DOWNLOAD_URL` 配置的位置

---

## 7. 运行步骤

```bash
# 1. 启动Milvus
docker-compose up -d

# 2. 确保MySQL服务运行中

# 3. 运行主程序
python main.py
```

然后访问：
- 前端：`http://localhost:80`
- 后端API文档：`http://localhost:8000/docs`

---

## 故障排除

| 问题 | 解决方案 |
|------|--------|
| 无法连接数据库 | 检查database.py中的连接字符串，确保MySQL服务运行 |
| 模型下载失败 | 检查网络连接，检查MODEL_DOWNLOAD_URL路径是否正确 |
| AI功能超时 | 确保在校园网或VPN环境，检查SERVER_IP配置 |
| Milvus连接失败 | 运行 `docker-compose up -d` 启动Milvus，确保Docker运行中 |
| 找不到上传文件 | 检查IMAGE_DIR和BASE_DIR路径配置是否正确 |

