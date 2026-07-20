import pandas as pd
from ragas import evaluate
# from baseline.prepare_dataset import prepare_dataset
from prepare_dataset import prepare_dataset
import os
os.environ["OPENAI_API_KEY"] = "sk-xxxxxxxxxxxxxxxxx"
from ragas.metrics import faithfulness, answer_relevancy, context_recall, context_precision, answer_correctness
from langchain_openai import ChatOpenAI

from langchain_openai import OpenAIEmbeddings

"""
因本人犯懒，所有路径都写的绝对路径，请自行修改.

跑指标挺快，我就直接跑完了.

评估代码，API_KEY替换为选定的LLM的token，LLM_NAME要对应修改.
结果记录在当前文件夹下的result.xlsx文件.
几个指标：
- 上下文精度（context precision） —— 均值：0.97
    - 上下文中与事实相关的条目是否排行较高
    - 越接近1越好
- 上下文召回率（context recall） —— 均值：0.875
    - 上下文和事实的相关性
    - 越接近1越好
- 忠实度（faithfulness） —— 均值：0.846
    - 生成的答案和上下文的事实一致性
    - 越接近1越好
- 答案相关性（Answer relevancy） —— 均值：0.54
    - 答案和问题的相关程度，不完整或者包含冗余会低分
    - 越接近1越好
- 答案准确性（answer correctness） —— 均值：0.643
    - 答案是否正确
    - 越接近1越好
"""

LLM_NAME = "gpt-4o-mini"
API_KEY = "sk-xxxxxxxxxxxxxxxxx"
data_url = "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\final_result\\fianl_data_without_rerank.json"
save_path = "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\final_result\\result_text_without_rerank.xlsx"

saved_df = None
if os.path.exists(save_path):
    saved_df = pd.read_excel(save_path, engine="openpyxl")

embedding_model = OpenAIEmbeddings(
    openai_api_key=API_KEY,
    openai_api_base="https://api.chatanywhere.tech/v1",
    model="text-embedding-3-small"
)

client = ChatOpenAI(
    model=LLM_NAME,
    api_key=API_KEY,
    base_url="https://api.chatanywhere.tech/v1",
    max_tokens=4096,
    request_timeout=60
)
# ragas_llm = llm_factory(model=LLM_NAME, client=client)


dataset = prepare_dataset(data_url, save_path)

result = evaluate(dataset,
                  metrics=[context_precision, context_recall, faithfulness],
                  llm=client, embeddings=embedding_model)
df = result.to_pandas()

if saved_df is not None:
    all_data = pd.concat([saved_df, df], ignore_index=True)
else:
    all_data = df

all_data.to_excel(save_path, index=False)