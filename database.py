from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
# 数据库连接URL（修改为你的MySQL配置）
SQLALCHEMY_DATABASE_URL = "mysql+asyncmy://root:your_password@localhost:your_port/your_database_name"

# 创建引擎
# engine = create_engine(SQLALCHEMY_DATABASE_URL)

# 创建SessionLocal类
# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=True, pool_size=10, max_overflow=20)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# 创建Base类
Base = declarative_base()

# 获取数据库连接的依赖函数
# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()  # 正常结束就提交
        except Exception:
            await session.rollback()
            raise