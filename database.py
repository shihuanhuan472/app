from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 数据库连接URL（修改为你的MySQL配置）
SQLALCHEMY_DATABASE_URL = "mysql+pymysql://root:cxx20040914@localhost:3306/repair"

# 创建引擎
engine = create_engine(SQLALCHEMY_DATABASE_URL)

# 创建SessionLocal类
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建Base类
Base = declarative_base()

# 获取数据库连接的依赖函数
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()