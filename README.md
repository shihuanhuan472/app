# 设备维修辅助系统

基于多模态RAG的维修辅助系统，总体上实现用户登录，用户管理，文档管理和ai问答辅助功能。因问答LLM部署在学校服务器，因此使用系统时，请<u>确保在校园网环境下，或挂载学校VPN</u>，否则ai连接超时，无法执行某些功能。

若想进行任何代码修改，请提前告知，或在master分支上新建dev分支，再从dev分支新建个人分支。请勿直接往master分支推送代码

**未经允许，代码禁止外传！！！**

## 项目结构

```
/Maintenance_Assistance_System/
├── app/                   # 应用程序
│   ├── bge/               # 嵌入模型
│   ├── datasets/          # 评估结果（具体每个文件干什么的，可以在evaluate/eval_picture.py里查看）
│   │     ├── final_result/  # 我毕设的最后评估结果，非该文件夹下的数据不完整或不准确
│   ├── embedding-model/   # 嵌入模型，可自行选择其他路径，在VectorStoreMultimodal.py中记得修改路径
│   ├── eval_images/       # 图像评估数据集
│   ├── evaluate/          # 评估代码
│   ├── routers/           # 各路由文件
│   ├── static/            # 前端代码
│   │     ├── css/
│   │     ├── js/
│   │     └── *.html
│   ├── upload/            # 上传的文件和图片
│   |     ├── ask/
│   |     ├── documents/
│   |     └── images/
│   ├── utils/             # 各种工具，包含各种格式文档解析器，JWT令牌生成器，Milvus操作等
│   ├── .env               # 环境配置
│   ├── database.py        # 数据库配置
│   ├── dependencies.py    # 相关依赖（其实就是拦截器的代码）
│   ├── docker-compose.yml # Milvus配置文件
│   ├── main.py            # 主程序
│   ├── models.py          # 数据模型
│   ├── requirements.txt   # 环境要求
│   └── schema.py          # 所有路由涉及到的模式
```



## 技术栈

前端：HTML + CSS + JS（因本人前端技术有限……）

后端：FastAPI + sqlalchemy

数据库：MySQL + Milvus

核心算法：

多模态RAG，对于一个文档Document，按照字段进行分块，并和图片一起向量化，存入Milvus，用户对话时，直接基于Milvus相似度检索，得到top_k块，进而得到相关文档，将相关文档内容作为提示词丢给AI。经过尝试，对于图像在检索前增加图像描述，能大幅提高图像检索的性能。在检索到结果后，借助LLM进行相似度打分，将该分数和MIlvus的相似度得分加权得到最后得分，实现重排序，能提升召回率。

对于直接导入的文档，因内容并未严格按照数据库预设的Document格式，因此导入时，将内容提取，让AI按照我的预设格式进行总结，进而将文件转为预设格式。并且在添加文档分块的时候，会调用LLM根据问题简介和成因，总结一下内容，形成一个主chunk（我本意是这里总结一些核心内容，方便检索，有一点点效果吧）

建议：

前端运行在80端口，后端运行在8000端口，MySQL运行在3306端口，Milvus运行在19530端口



## 数据库设计

数据库选用MySQL，具体数据表参考**数据库表设计.md**文件（因为开发中期修改过数据库表，documents表注意用后面一版）

注意：系统部署完后，运行前，记得往MySQL里插入一下用户数据，不然登录都没得登录，插入格式参考如下（密码为123456，下方内容中密码为md5加密后的结果）：

```sql
INSERT INTO users (username, password, phone, email, full_name, status, role, department, created_time, last_login)
VALUES ('admin', 'e10adc3949ba59abbe56e057f20f883e', '17812355311', 'admin@whut.edu.com', '管理员', 1, 0, '管理部', NOW(), NOW());
```

本地数据库创建后，需在database.py中修改具体配置，否则无法连接本地数据库

```python
SQLALCHEMY_DATABASE_URL = "mysql+asyncmy://root:your_password@localhost:your_port/your_database_name"
```

`your_password`：本地数据库密码

`your_port`：数据库占用端口（一般默认是3306，如果不是则使用你设置的端口）

`your_database_name`：即该系统对应数据库名字

`root`：如果该系统你的数据库权限不是root，则更改为对应用户

`localhost`：若不使用本地数据库，而是某个服务器的数据库，则改为对应IP

---

**Milvus安装：**

请确保电脑已安装docker

1. 下载配置文件

   项目已提供docker-compose.yml文件，也可以自己去下载该配置文件

   下载配置文件：

   ```bash
   # macOS / Linux (使用 wget)
   wget https://github.com/milvus-io/milvus/releases/download/v2.5.14/milvus-standalone-docker-compose.yml -O docker-compose.yml
   
   # Windows (使用 PowerShell)
   Invoke-WebRequest -Uri "https://github.com/milvus-io/milvus/releases/download/v2.5.14/milvus-standalone-docker-compose.yml" -OutFile "docker-compose.yml"
   ```

2. 启动milvus服务

   在docker-compose.yml所在的文件夹，执行以下命令（如果拉取得特别慢，请自行使用魔法）：

   ```bash
   docker compose up -d
   ```

   运行完就会发现多了一个`volumes`文件夹，里面包含三个子文件夹`etcd`，`milvus`和`minio`。

   （milvus有点吃内存，不过现在电脑都是16GB以上了，应该没问题）

   ~~（如果实在拉取不来，可以找我要tar，然后直接用docker去启动）~~

3. 验证安装是否成功

   在终端执行以下命令：

   ```bash
   docker ps
   ```

   或者去docker desktop查看

   milvus默认使用的是19530端口，若修改为其他端口，请在`VectorStoreMultimodal.py`文件的连接数据库部分修改为对应端口

后续每次运行系统前，请**确保Milvus服务被开启了**，否则后端无法连接Milvus（Milvus和MySQL不同，一般不设置开机自启）



## 系统部署

系统后端基于FastAPI框架，Python版本建议3.9及以上，后端开发是基于Python3.10的

`requirements.txt`中包含开发过程中使用的所有包环境，因前后开发出现了模型更换，技术修改等问题，因此部分包实际不再需要，如：jieba，并且torch安装可基于本机环境进行其他版本的安装，因此requirements.txt仅做参考，若安装过程中出现报错，请自行查找解决措施，多为库内部错误。

若想直接使用`requirements.txt`进行包安装，可以使用以下语句：

```bash
pip install -r requirements.txt
```



### 嵌入模型安装

向量嵌入模型选用BAAI/bge-visualized，详细介绍见下方链接，并从下方链接的仓库中下载权重文件Visualized_m3.pth，权重文件放在`bge`文件夹下。

[BAAI/bge-visualized · Hugging Face](https://huggingface.co/BAAI/bge-visualized)

具体的安装流程请参考下发链接
[Visualized_BGE 安装—多模态嵌入技术_visual bge-CSDN博客](https://blog.csdn.net/weixin_44190648/article/details/148651418)

程序第一次运行的时候会自动下载模型，下载的地址是`bge/model`文件夹，切记要去.env中修改地址，不然找不到地址，很可能把模型丢去C盘了（当然如果C盘空间很大，就当我没说）

下载完成后，`bge/model`文件夹中会多一些内容，就是模型，具体是什么内容不用管



### HTML解析器浏览器支持

因为html文档中存在大量图片，若使用图片的src进行图片下载，重要图片均会自动跳转登录界面，无法正常下载。因此采用了对文档进行渲染以得到图片的方式

请先确保本地电脑有**chrome浏览器**，若没有，需自行安装（经过测试，Chrome114及其对应的ChromeDriver是可以稳定运行的，特别新的版本有可能会报错）

程序运行的时候不会真的打开chrome浏览器，只是需要浏览器渲染



### 前端部署

若本地运行及访问，前端实际不需要特别的部署

---

若在服务器等地方运行，则需要将前端静态资源反向代理，请确保电脑安装有Nginx

具体配置参考如下（若本地运行可以跳过，以下为我服务器配置Nginx文件）：

```bash
vim /etc/nginx/conf.d/maintenance.conf

server {
    listen 80;
    server_name 172.28.114.47 localhost 127.0.0.1;
    
    client_max_body_size 10M;
    
    # 根目录指向 static 文件夹
    root /data/Maintenance_Assistance_System/static;
    index index.html;
    
    # 前端路由支持（单页应用）
    location / {
        try_files $uri $uri/ /index.html;
    }
    
    # 上传文件服务（保持原样）
    location ^~/upload/ {
        alias /data/Maintenance_Assistance_System/upload/;
        expires 30d;
    }

    
    # 静态文件缓存优化
    location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        access_log off;
    }
    
    # 后端API代理
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # 认证接口（如果有）
    location /auth/ {
        proxy_pass http://127.0.0.1:8000/auth/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # CORS 头
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Methods "GET, POST, PATCH, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # 处理 OPTIONS 预检请求
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With";
            add_header Access-Control-Max-Age 1728000;
            add_header Content-Type 'text/plain charset=UTF-8';
            add_header Content-Length 0;
            return 204;
        }
    }
    
    location /user/ {
        proxy_pass http://127.0.0.1:8000/user/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # CORS 头
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Methods "GET, POST, PATCH, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # 处理 OPTIONS 预检请求
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With";
            add_header Access-Control-Max-Age 1728000;
            add_header Content-Type 'text/plain charset=UTF-8';
            add_header Content-Length 0;
            return 204;
        }
    }
    
    location /admin/ {
        proxy_pass http://127.0.0.1:8000/admin/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # CORS 头
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Methods "GET, POST, PATCH, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # 处理 OPTIONS 预检请求
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With";
            add_header Access-Control-Max-Age 1728000;
            add_header Content-Type 'text/plain charset=UTF-8';
            add_header Content-Length 0;
            return 204;
        }
    }
    
    location /conversation/ {
        proxy_pass http://127.0.0.1:8000/conversation/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # CORS 头
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Methods "GET, POST, PATCH, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # 处理 OPTIONS 预检请求
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With";
            add_header Access-Control-Max-Age 1728000;
            add_header Content-Type 'text/plain charset=UTF-8';
            add_header Content-Length 0;
            return 204;
        }
    }
    
    location /message/ {
        proxy_pass http://127.0.0.1:8000/message/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # CORS 头
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Methods "GET, POST, PATCH, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # 处理 OPTIONS 预检请求
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With";
            add_header Access-Control-Max-Age 1728000;
            add_header Content-Type 'text/plain charset=UTF-8';
            add_header Content-Length 0;
            return 204;
        }
    }
    
    location /document/ {
        proxy_pass http://127.0.0.1:8000/document/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # CORS 头
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Methods "GET, POST, PATCH, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # 处理 OPTIONS 预检请求
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With";
            add_header Access-Control-Max-Age 1728000;
            add_header Content-Type 'text/plain charset=UTF-8';
            add_header Content-Length 0;
            return 204;
        }
    }
    
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 600s;
        
        # CORS 头
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Methods "GET, POST, PATCH, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # 处理 OPTIONS 预检请求
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With";
            add_header Access-Control-Max-Age 1728000;
            add_header Content-Type 'text/plain charset=UTF-8';
            add_header Content-Length 0;
            return 204;
        }
    }

    location /chatbot/ {
        proxy_pass http://192.168.246.223/chatbot/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # CORS 头
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Methods "GET, POST, PATCH, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # 处理 OPTIONS 预检请求
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With";
            add_header Access-Control-Max-Age 1728000;
            add_header Content-Type 'text/plain charset=UTF-8';
            add_header Content-Length 0;
            return 204;
        }
    }

    # WebSocket支持（如果需要）
    location /ws/ {
        proxy_pass http://127.0.0.1:8000/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # CORS 头
        add_header Access-Control-Allow-Origin "$http_origin" always;
        add_header Access-Control-Allow-Methods "GET, POST, PATCH, PUT, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With" always;
        add_header Access-Control-Allow-Credentials "true" always;
        
        # 处理 OPTIONS 预检请求
        if ($request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin "$http_origin";
            add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS";
            add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With";
            add_header Access-Control-Max-Age 1728000;
            add_header Content-Type 'text/plain charset=UTF-8';
            add_header Content-Length 0;
            return 204;
        }
    }
    
    # 禁止访问隐藏文件
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
    
    # 禁止访问敏感文件
    location ~* \.(log|sh|py|sql|env)$ {
        deny all;
    }
}
```

---



**本地运行：**

<u>在static文件夹下</u>，运行以下命令，则可以通过localhost/index.html访问到登录界面

```bash
python -m http.server 80
```

`80`：前端端口

切记要进入static文件夹下

按`ctrl + c`则可停止运行



### 后端运行

后端运行之前，切记在**.env**文件中进行具体环境变量的修改，**AI服务器的相关配置不要改动**，其他路径请根据项目具体路径进行改动

若运行出现问题，尤其是图片，文档的存储路径并不符合预期，或者直接报错，可以尝试把系统中所有os.getenv()再根据项目具体情况修改一下



如果在ide中，直接运行后端即可，或者在命令行中使用如下命令

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

（注意：需与main.py在同一文件夹中）

若想在后台运行，可执行如下指令

```bash
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > uvicorn.log 2>&1 &
```

`8000`：后端端口占用



因加载一些模型和Milvus数据库，后端运行需要几分钟的时间，请在后端运行彻底完成后再登录



### 其它内容

评估详情可以看评估结果文件，需要注意，若使用写好的脚本导入文件，前端看不到相关文件，影响不大。

若启动整个项目，阈值可在.env文件中修改，若仅跑评估代码，需去VectorService.py或VectorStoreMultimodal.py修改os.getenv函数中的数据

因为本人比较懒，所以所有路径写的都是绝对路径，请自行修改

本人对并发上的知识过于弱了，前期开发使用的同步数据库，导致并发功能特别弱，全是阻塞。后期改成了异步，稍微好了一些，但是不确定有没有改出Bug。

推送的过程满是我瞎撞的结果，代码里也会包含一些废弃掉的代码，但是不一定删了。

后端模型的输入有点小，限制8192个token，所以太大的文件确实导不进去……



from cxx

初稿：2026.3.17

最新更新：2026.4.27
